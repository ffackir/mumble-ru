"""A process-owned Windows mutex ties the DLL to the companion's lifetime.

The OS releases ownership on a crash too, so no persistent 'enabled' flag
can accidentally leave capture running after the companion exits.
"""
import ctypes
import os

MUTEX_NAME = 'Local\\MumbleRU.Companion.v1'

class CompanionLease:
    def __init__(self, reset_stop=True):
        self.handle = None
        self.stop_handle = None
        self.parent_handle = None
        self.parent_missing = False
        if os.name != 'nt':
            return
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateEventW.restype = ctypes.c_void_p
        self.kernel = kernel
        handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        result = kernel.WaitForSingleObject(handle, 1000)
        if result not in (0, 0x80):
            kernel.CloseHandle(handle)
            raise RuntimeError('Mumble RU уже запущен или занят канал управления.')
        self.handle = handle
        self.stop_handle = kernel.CreateEventW(None, True, False, 'Local\\MumbleRU.Stop.v1')
        if not self.stop_handle:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())
        # Ignore an old shutdown from the previous Mumble session.
        kernel.ResetEvent.argtypes = [ctypes.c_void_p]
        if reset_stop:
            kernel.ResetEvent(self.stop_handle)

    def watch_parent(self, pid):
        self.kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        self.kernel.OpenProcess.restype = ctypes.c_void_p
        self.parent_handle = self.kernel.OpenProcess(0x00100000, False, pid)
        self.parent_missing = not bool(self.parent_handle)

    def shutdown_requested(self):
        return (self.parent_missing or
                bool(self.parent_handle and self.kernel.WaitForSingleObject(self.parent_handle, 0) != 258) or
                bool(self.stop_handle and self.kernel.WaitForSingleObject(self.stop_handle, 0) == 0))

    def close(self):
        if self.parent_handle:
            self.kernel.CloseHandle(self.parent_handle)
            self.parent_handle = None
        if self.stop_handle:
            self.kernel.CloseHandle(self.stop_handle)
            self.stop_handle = None
        if self.handle:
            self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None
