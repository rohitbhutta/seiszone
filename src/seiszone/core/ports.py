"""Who holds a listener port, and freeing the ports of the old Gecko SeedLink service (Windows).

An older program, the Windows service "GeckoSeedLink" (the old Gecko SeedLink service), listens
on the Gecko port 58772 (and its own SeedLink port 18010). While it runs, the Kelunji Gecko
connects to it and SeisZone's own Gecko receiver cannot open its port. SeisZone's SeedLink server
uses 18000 by default; it clashes with the old service only if it is set to 18010.

  * port_owner(port)      process id of the program listening on a TCP port (IPv4 or IPv6)
  * service_pid(name)     process id of a running Windows service
  * describe_owner(port)  ("old-service", pid) | ("other", pid) | (None, None)
  * stop_old_service()    stops and disables the old service, after one administrator
                          prompt of Windows (UAC); it is not uninstalled, so it can be
                          switched on again in Windows Services

The queries are read only (ctypes, no extra package). Outside Windows every function is a
harmless no-op (None / False). Process or program names are never looked up or shown: the old
service is known only by its service name.
"""

import ctypes
import logging
import os
import socket
import struct
import sys

log = logging.getLogger("seiszone")

OLD_SERVICE = "GeckoSeedLink"

AF_INET, AF_INET6 = 2, 23
TCP_TABLE_OWNER_PID_LISTENER = 3
MIB_TCP_STATE_LISTEN = 2
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_CANCELLED = 1223            # the user answered No to the administrator prompt
SE_ERR_ACCESSDENIED = 5           # what ShellExecuteW returns then
SC_MANAGER_CONNECT = 0x0001
SERVICE_QUERY_STATUS = 0x0004
SC_STATUS_PROCESS_INFO = 0
SERVICE_STOPPED = 1
SW_HIDE = 0

ROW4 = struct.Struct("<6I")       # MIB_TCPROW_OWNER_PID: state, addr, port, raddr, rport, pid
ROW6 = struct.Struct("<16sII16sIIII")  # MIB_TCP6ROW_OWNER_PID: addr, scope, port, raddr, rscope, rport, state, pid
SERVICE_STATUS_PROCESS = struct.Struct("<9I")   # ... dwCurrentState is [1], dwProcessId is [7]


def parse_tcp_table(buf, family):
    """[(local port, pid)] of the LISTEN rows in a GetExtendedTcpTable buffer
    (MIB_TCPTABLE_OWNER_PID for AF_INET, MIB_TCP6TABLE_OWNER_PID for AF_INET6)."""
    if len(buf) < 4:
        return []
    n = struct.unpack_from("<I", buf, 0)[0]
    out = []
    if family == AF_INET:
        for i in range(n):
            off = 4 + i * ROW4.size
            if off + ROW4.size > len(buf):
                break
            state, _addr, port, _ra, _rp, pid = ROW4.unpack_from(buf, off)
            if state == MIB_TCP_STATE_LISTEN:
                out.append((socket.ntohs(port & 0xFFFF), pid))
    else:
        for i in range(n):
            off = 4 + i * ROW6.size
            if off + ROW6.size > len(buf):
                break
            _addr, _scope, port, _ra, _rs, _rp, state, pid = ROW6.unpack_from(buf, off)
            if state == MIB_TCP_STATE_LISTEN:
                out.append((socket.ntohs(port & 0xFFFF), pid))
    return out


def _tcp_table(family):
    """The raw listener table of one address family (b"" when it cannot be read)."""
    from ctypes import wintypes
    iphlp = ctypes.WinDLL("iphlpapi")
    fn = iphlp.GetExtendedTcpTable
    fn.restype = wintypes.DWORD
    fn.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL, wintypes.ULONG,
                   ctypes.c_int, wintypes.ULONG)
    size = wintypes.DWORD(0)
    for _ in range(5):  # the table can grow between the size query and the read
        buf = ctypes.create_string_buffer(max(size.value, 4))
        rc = fn(buf, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_LISTENER, 0)
        if rc == 0:
            return buf.raw
        if rc != ERROR_INSUFFICIENT_BUFFER:
            return b""
    return b""


def listeners():
    """[(port, pid)] of every TCP listener on this PC, IPv4 and IPv6 ([] outside Windows)."""
    if sys.platform != "win32":
        return []
    out = []
    for family in (AF_INET, AF_INET6):
        try:
            out += parse_tcp_table(_tcp_table(family), family)
        except (OSError, AttributeError, struct.error):
            pass
    return out


def port_owner(port):
    """Process id of the program listening on TCP `port`, None when nobody (or unknown)."""
    for p, pid in listeners():
        if p == port and pid:
            return pid
    return None


def service_pid(name):
    """Process id of the Windows service `name` while it runs, else None (read-only query)."""
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        adv.OpenSCManagerW.restype = wintypes.HANDLE
        adv.OpenSCManagerW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        adv.OpenServiceW.restype = wintypes.HANDLE
        adv.OpenServiceW.argtypes = (wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD)
        adv.QueryServiceStatusEx.restype = wintypes.BOOL
        adv.QueryServiceStatusEx.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD))
        adv.CloseServiceHandle.argtypes = (wintypes.HANDLE,)
        scm = adv.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
        if not scm:
            return None
        try:
            svc = adv.OpenServiceW(scm, name, SERVICE_QUERY_STATUS)
            if not svc:  # not installed (or not visible to this user)
                return None
            try:
                buf = ctypes.create_string_buffer(SERVICE_STATUS_PROCESS.size)
                need = wintypes.DWORD(0)
                if not adv.QueryServiceStatusEx(svc, SC_STATUS_PROCESS_INFO, buf, len(buf), ctypes.byref(need)):
                    return None
                fields = SERVICE_STATUS_PROCESS.unpack(buf.raw)
                state, pid = fields[1], fields[7]
                return pid if pid and state != SERVICE_STOPPED else None
            finally:
                adv.CloseServiceHandle(svc)
        finally:
            adv.CloseServiceHandle(scm)
    except (OSError, AttributeError):
        return None


def describe_owner(port):
    """("old-service", pid) when the old Gecko SeedLink service holds `port`, ("other", pid) for
    any other program, (None, None) when the holder cannot be found."""
    pid = port_owner(port)
    if pid is None:
        return None, None
    if service_pid(OLD_SERVICE) == pid:
        return "old-service", pid
    return "other", pid


def _system32():
    return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32")


def _shell_execute(hwnd, verb, file, params, directory, show):
    """ShellExecuteW -> (its return value, GetLastError). Kept apart so tests replace it."""
    from ctypes import wintypes
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    fn = shell32.ShellExecuteW
    fn.restype = ctypes.c_void_p
    fn.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                   ctypes.c_int)
    rc = fn(hwnd, verb, file, params, directory, show)
    return (rc or 0), ctypes.get_last_error()


def stop_command():
    """(program, parameters) that stop the old service and keep it off (start= disabled). A
    clean stop is not a failure, so its "restart on failure" actions do not bring it back."""
    return (os.path.join(_system32(), "cmd.exe"),
            f"/c sc stop {OLD_SERVICE} & sc config {OLD_SERVICE} start= disabled")


def stop_old_service():
    """Stop and disable the old Gecko SeedLink service with one administrator prompt of
    Windows. True when the elevated command was started; False when the user said No or it
    could not be started (outside Windows: always False). The command runs hidden and on its
    own: the caller sees the result when the port becomes free."""
    if sys.platform != "win32":
        return False
    program, params = stop_command()
    try:
        rc, err = _shell_execute(None, "runas", program, params, None, SW_HIDE)
    except (OSError, AttributeError) as e:
        log.warning("the old Gecko SeedLink service could not be stopped (%s)", e)
        return False
    if rc > 32:
        log.info("stopping and disabling the old Gecko SeedLink service (administrator permission given)")
        return True
    if err == ERROR_CANCELLED or rc == SE_ERR_ACCESSDENIED:
        log.warning("the old Gecko SeedLink service keeps running: administrator permission was not given")
    else:
        log.warning("the old Gecko SeedLink service could not be stopped (error %d / %d)", rc, err)
    return False
