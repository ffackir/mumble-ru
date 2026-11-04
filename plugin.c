/* Mumble RU: non-blocking, receive-only audio bridge. MIT. */
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <wchar.h>
#include "vendor/MumblePlugin.h"

#define PORT 47831
#define CAPACITY 512
#define PAYLOAD 1024
/* Little endian Windows x64; same wire layout as Python <4sB3xIIII. */
typedef struct {
    char magic[4]; uint8_t type; uint8_t reserved[3];
    uint32_t connection, user, sequence, rate;
    unsigned char payload[PAYLOAD];
} Packet;
typedef struct { Packet packet; int size; } Item;
static Item items[CAPACITY];
static unsigned head, tail;
static CRITICAL_SECTION lock;
static HANDLE wake, worker, companion, companion_stop;
static volatile LONG running, connection, sequence, active;
static SOCKET udp = INVALID_SOCKET;
static struct sockaddr_in destination;
static MumbleAPI api;
static mumble_plugin_id_t own_id;

static void start_companion(void) {
    DWORD owner = WaitForSingleObject(companion, 0);
    if (owner == WAIT_TIMEOUT) return; /* Already running. */
    if (owner != WAIT_OBJECT_0 && owner != WAIT_ABANDONED) return;
    ReleaseMutex(companion);
    WCHAR python[4096], script[4096], command[8300];
    DWORD python_size = sizeof(python), script_size = sizeof(script);
    if (RegGetValueW(HKEY_CURRENT_USER, L"Software\\MumbleRU", L"Python", RRF_RT_REG_SZ,
                     NULL, python, &python_size) != ERROR_SUCCESS ||
        RegGetValueW(HKEY_CURRENT_USER, L"Software\\MumbleRU", L"Script", RRF_RT_REG_SZ,
                     NULL, script, &script_size) != ERROR_SUCCESS) return;
    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES ||
        GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) return;
    if (wcschr(python, L'"') || wcschr(script, L'"')) return;
    swprintf(command, 8300, L"\"%ls\" \"%ls\" --managed --mumble-pid %lu", python, script, GetCurrentProcessId());
    STARTUPINFOW startup = {0}; startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW; startup.wShowWindow = SW_SHOWNORMAL;
    PROCESS_INFORMATION process = {0};
    if (CreateProcessW(python, command, NULL, NULL, FALSE, CREATE_NO_WINDOW,
                       NULL, NULL, &startup, &process)) {
        CloseHandle(process.hThread); CloseHandle(process.hProcess);
    }
}

static Packet packet(uint8_t type, uint32_t conn, uint32_t user, uint32_t rate) {
    Packet p = {0};
    memcpy(p.magic, "MRU1", 4); p.type = type; p.connection = conn;
    p.user = user; p.rate = rate; p.sequence = (uint32_t) InterlockedIncrement(&sequence);
    return p;
}
static void push(const Packet *p, int payload_size) {
    if (!InterlockedCompareExchange(&running, 0, 0) || !InterlockedCompareExchange(&active, 0, 0)
        || !TryEnterCriticalSection(&lock)) return;
    unsigned next = (head + 1) % CAPACITY;
    if (next != tail) {
        items[head].packet = *p; items[head].size = 24 + payload_size; head = next;
    }
    LeaveCriticalSection(&lock);
    SetEvent(wake);
}
static DWORD WINAPI transport(void *unused) {
    (void) unused;
    DWORD last_heartbeat = 0;
    while (InterlockedCompareExchange(&running, 0, 0)) {
        WaitForSingleObject(wake, 50);
        DWORD owner = WaitForSingleObject(companion, 0);
        bool enabled = owner == WAIT_TIMEOUT;
        if (owner == WAIT_OBJECT_0 || owner == WAIT_ABANDONED) ReleaseMutex(companion);
        InterlockedExchange(&active, enabled ? 1 : 0);
        if (!enabled) {
            EnterCriticalSection(&lock); tail = head; LeaveCriticalSection(&lock);
            continue;
        }
        for (;;) {
            Item item;
            EnterCriticalSection(&lock);
            if (tail == head) { LeaveCriticalSection(&lock); break; }
            item = items[tail]; tail = (tail + 1) % CAPACITY;
            LeaveCriticalSection(&lock);
            sendto(udp, (const char *) &item.packet, item.size, 0,
                   (const struct sockaddr *) &destination, sizeof(destination));
        }
        DWORD now = GetTickCount();
        if (now - last_heartbeat >= 1000) {
            Packet p = packet(5, (uint32_t) InterlockedCompareExchange(&connection, 0, 0), 0, 0);
            sendto(udp, (const char *) &p, 24, 0, (const struct sockaddr *) &destination, sizeof(destination));
            last_heartbeat = now;
        }
    }
    return 0;
}
static struct MumbleStringWrapper text(const char *value) {
    struct MumbleStringWrapper s = {value, strlen(value), false}; return s;
}
mumble_error_t mumble_init(mumble_plugin_id_t id) {
    own_id = id;
    WSADATA data;
    if (WSAStartup(MAKEWORD(2, 2), &data)) return MUMBLE_EC_GENERIC_ERROR;
    udp = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (udp == INVALID_SOCKET) { WSACleanup(); return MUMBLE_EC_GENERIC_ERROR; }
    u_long nonblocking = 1;
    ioctlsocket(udp, FIONBIO, &nonblocking);
    memset(&destination, 0, sizeof(destination)); destination.sin_family = AF_INET;
    destination.sin_port = htons(PORT); destination.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    InitializeCriticalSection(&lock); head = tail = 0;
    InterlockedExchange(&active, 0);
    companion = CreateMutexW(NULL, FALSE, L"Local\\MumbleRU.Companion.v1");
    if (!companion) { DeleteCriticalSection(&lock); closesocket(udp); WSACleanup(); return MUMBLE_EC_GENERIC_ERROR; }
    companion_stop = CreateEventW(NULL, TRUE, FALSE, L"Local\\MumbleRU.Stop.v1");
    if (!companion_stop) { CloseHandle(companion); DeleteCriticalSection(&lock); closesocket(udp); WSACleanup(); return MUMBLE_EC_GENERIC_ERROR; }
    ResetEvent(companion_stop);
    wake = CreateEventW(NULL, FALSE, FALSE, NULL);
    if (!wake) { CloseHandle(companion_stop); CloseHandle(companion); DeleteCriticalSection(&lock); closesocket(udp); WSACleanup(); return MUMBLE_EC_GENERIC_ERROR; }
    mumble_connection_t active = 0;
    if (api.getActiveServerConnection && api.getActiveServerConnection(own_id, &active) == MUMBLE_STATUS_OK)
        InterlockedExchange(&connection, (LONG) active);
    InterlockedExchange(&running, 1);
    worker = CreateThread(NULL, 0, transport, NULL, 0, NULL);
    if (!worker) {
        InterlockedExchange(&running, 0); CloseHandle(wake); CloseHandle(companion_stop); CloseHandle(companion); DeleteCriticalSection(&lock);
        closesocket(udp); WSACleanup(); return MUMBLE_EC_GENERIC_ERROR;
    }
    if (api.getActiveServerConnection) start_companion();
    return MUMBLE_STATUS_OK;
}
void mumble_shutdown(void) {
    if (!InterlockedExchange(&running, 0)) return;
    InterlockedExchange(&active, 0);
    SetEvent(companion_stop);
    SetEvent(wake); WaitForSingleObject(worker, INFINITE);
    CloseHandle(worker); CloseHandle(wake); CloseHandle(companion_stop); CloseHandle(companion); closesocket(udp); udp = INVALID_SOCKET;
    DeleteCriticalSection(&lock); WSACleanup();
}
struct MumbleStringWrapper mumble_getName(void) { return text("Mumble RU"); }
struct MumbleStringWrapper mumble_getAuthor(void) { return text("Mumble RU project"); }
struct MumbleStringWrapper mumble_getDescription(void) {
    return text("Personal Russian captions with speaker names. Start the companion overlay. Received audio is sent only to 127.0.0.1:47831; original sound is unchanged.");
}
mumble_version_t mumble_getAPIVersion(void) { return MUMBLE_PLUGIN_API_VERSION; }
mumble_version_t mumble_getVersion(void) { mumble_version_t v = {1, 1, 0}; return v; }
void mumble_registerAPIFunctions(void *functions) { api = *((MumbleAPI *) functions); }
void mumble_releaseResource(const void *pointer) { (void) pointer; }
uint32_t mumble_getFeatures(void) { return MUMBLE_FEATURE_AUDIO; }
static void name_for(mumble_connection_t conn, mumble_userid_t user) {
    const char *name = NULL;
    if (api.getUserName && api.getUserName(own_id, conn, user, &name) == MUMBLE_STATUS_OK) {
        Packet p = packet(2, conn, user, 0);
        size_t len = strlen(name); if (len > PAYLOAD) len = PAYLOAD;
        memcpy(p.payload, name, len); push(&p, (int) len); api.freeMemory(own_id, name);
    }
}
void mumble_onServerSynchronized(mumble_connection_t conn) { InterlockedExchange(&connection, (LONG) conn); }
void mumble_onServerDisconnected(mumble_connection_t conn) {
    Packet p = packet(4, conn, 0, 0); push(&p, 0); InterlockedExchange(&connection, 0);
}
void mumble_onUserTalkingStateChanged(mumble_connection_t conn, mumble_userid_t user, mumble_talking_state_t state) {
    if (!InterlockedCompareExchange(&active, 0, 0)) return;
    name_for(conn, user);
    Packet p = packet(3, conn, user, (uint32_t) state); push(&p, 0);
}
bool mumble_onAudioSourceFetched(float *pcm, uint32_t count, uint16_t channels, uint32_t rate,
                               bool speech, mumble_userid_t user) {
    if (!InterlockedCompareExchange(&active, 0, 0) || !speech || !channels || channels > 32 || !pcm) return false;
    uint32_t conn = (uint32_t) InterlockedCompareExchange(&connection, 0, 0);
    for (uint32_t offset = 0; offset < count; offset += PAYLOAD / 2) {
        uint32_t n = count - offset; if (n > PAYLOAD / 2) n = PAYLOAD / 2;
        Packet p = packet(1, conn, user, rate);
        int16_t *out = (int16_t *) p.payload;
        for (uint32_t i = 0; i < n; ++i) {
            float sample = 0;
            for (uint16_t c = 0; c < channels; ++c) sample += pcm[(offset + i) * channels + c];
            sample /= channels;
            if (!isfinite(sample)) sample = 0;
            if (sample > 1) sample = 1; if (sample < -1) sample = -1;
            out[i] = (int16_t) (sample * 32767);
        }
        push(&p, (int) (n * 2));
    }
    return false; /* Never modify sound or transmit microphone audio. */
}
