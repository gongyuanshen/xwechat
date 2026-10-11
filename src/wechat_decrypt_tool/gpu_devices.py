"""Windows GPU inventory and persistent PnP identity resolution."""
from __future__ import annotations

import ctypes as ct
import re
import sys
import uuid


class _LUID(ct.Structure):
    _fields_ = [('LowPart', ct.c_uint32), ('HighPart', ct.c_int32)]

    def key(self):
        return f'{self.HighPart & 0xFFFFFFFF:08x}:{self.LowPart:08x}'


class _AdapterDesc1(ct.Structure):
    _fields_ = [('Description', ct.c_wchar * 128), ('VendorId', ct.c_uint32),
                ('DeviceId', ct.c_uint32), ('SubSysId', ct.c_uint32),
                ('Revision', ct.c_uint32), ('DedicatedVideoMemory', ct.c_size_t),
                ('DedicatedSystemMemory', ct.c_size_t), ('SharedSystemMemory', ct.c_size_t),
                ('AdapterLuid', _LUID), ('Flags', ct.c_uint32)]


class _OpenAdapter(ct.Structure):
    _fields_ = [('AdapterLuid', _LUID), ('hAdapter', ct.c_uint32)]


class _QueryAdapter(ct.Structure):
    _fields_ = [('hAdapter', ct.c_uint32), ('Type', ct.c_uint32),
                ('pPrivateDriverData', ct.c_void_p), ('PrivateDriverDataSize', ct.c_uint32)]


class _PnpKey(ct.Structure):
    _fields_ = [('PhysicalAdapterIndex', ct.c_uint32), ('PnPKeyType', ct.c_uint32),
                ('pDest', ct.c_void_p), ('pCchDest', ct.POINTER(ct.c_uint32))]


class _PropertyKey(ct.Structure):
    _fields_ = [('fmtid', ct.c_ubyte * 16), ('pid', ct.c_uint32)]

    def __init__(self, guid, pid):
        super().__init__((ct.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le), pid)


class _IndirectDisplayInfo(ct.Structure):
    _fields_ = [('DisplayAdapterLuid', _LUID), ('Flags', ct.c_uint32),
                ('NumMonitors', ct.c_uint32), ('DisplayAdapterTargetBase', ct.c_uint32),
                ('DriverVersionMajor', ct.c_uint32), ('DriverVersionMinor', ct.c_uint32)]


class DeviceDetectionError(RuntimeError):
    """Hardware discovery or exact identity resolution failed."""
    category = 'gpu_init'


def _check(code, operation):
    if code != 0:
        raise DeviceDetectionError(f'{operation} failed (0x{code & 0xFFFFFFFF:08X})')


def _com(pointer, slot, result_type, *argument_types):
    table = ct.cast(pointer, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
    return ct.WINFUNCTYPE(result_type, ct.c_void_p, *argument_types)(table[slot])


def _identity_from_pnp_key(key):
    # Hardware keys are returned below Enum, including the Device Parameters key.
    # Recognize this layout explicitly; CM_Locate_DevNodeW verifies the exact ID.
    match = re.fullmatch(r'\\Registry\\Machine\\System\\(?:CurrentControlSet|ControlSet\d{3})\\Enum\\'
                         r'([^\\]+\\[^\\]+\\[^\\]+)\\Device Parameters', key, re.IGNORECASE)
    if not match:
        raise DeviceDetectionError(f'Invalid physical adapter PnP key: {key!r}')
    return match[1].upper()


def _kernel_details(luid, indirect_devices):
    # WinSDK d3dkmthk.h: adapter type=15, UMD version=18, physical count=30,
    # physical PnP key=41; hardware key kind=1 (not zero).
    gdi = ct.WinDLL('gdi32', use_last_error=True)
    for name in ('D3DKMTOpenAdapterFromLuid', 'D3DKMTQueryAdapterInfo', 'D3DKMTCloseAdapter'):
        function = getattr(gdi, name)
        function.argtypes, function.restype = [ct.c_void_p], ct.c_int32
    opened = _OpenAdapter(luid, 0)
    _check(gdi.D3DKMTOpenAdapterFromLuid(ct.byref(opened)), f'D3DKMTOpenAdapterFromLuid({luid.key()})')
    try:
        def query(kind, value):
            request = _QueryAdapter(opened.hAdapter, kind, ct.addressof(value), ct.sizeof(value))
            _check(gdi.D3DKMTQueryAdapterInfo(ct.byref(request)),
                   f'D3DKMTQueryAdapterInfo(type={kind}, luid={luid.key()})')

        adapter_type, count, driver = ct.c_uint32(), ct.c_uint32(), ct.c_uint64()
        query(15, adapter_type)
        if adapter_type.value & 64:
            if luid.key() not in indirect_devices:
                raise DeviceDetectionError(f'No PnP identity for indirect display LUID {luid.key()}')
            return {**indirect_devices[luid.key()], 'adapter_type': adapter_type.value}
        query(30, count)
        if count.value != 1:
            raise DeviceDetectionError(f'Adapter {luid.key()} has ambiguous physical adapter count: {count.value}')
        destination = ct.create_unicode_buffer(4096)
        length = ct.c_uint32(len(destination))
        pnp = _PnpKey(0, 1, ct.addressof(destination), ct.pointer(length))
        query(41, pnp)
        identity = _identity_from_pnp_key(destination.value)
        _check_devnode(identity)
        query(18, driver)
        version = '.'.join(str((driver.value >> shift) & 0xFFFF) for shift in (48, 32, 16, 0))
        return {'identity': identity, 'adapter_type': adapter_type.value,
                'driver': version if driver.value else None}
    finally:
        handle = ct.c_uint32(opened.hAdapter)
        _check(gdi.D3DKMTCloseAdapter(ct.byref(handle)), f'D3DKMTCloseAdapter({luid.key()})')


def _check_devnode(identity):
    cfg = ct.WinDLL('cfgmgr32', use_last_error=True)
    cfg.CM_Locate_DevNodeW.argtypes = [ct.POINTER(ct.c_uint32), ct.c_wchar_p, ct.c_uint32]
    cfg.CM_Locate_DevNodeW.restype = ct.c_uint32
    cfg.CM_Get_DevNode_Status.argtypes = [ct.POINTER(ct.c_uint32), ct.POINTER(ct.c_uint32), ct.c_uint32, ct.c_uint32]
    cfg.CM_Get_DevNode_Status.restype = ct.c_uint32
    node, status, problem = ct.c_uint32(), ct.c_uint32(), ct.c_uint32()
    _check(cfg.CM_Locate_DevNodeW(ct.byref(node), identity, 0), f'CM_Locate_DevNodeW({identity})')
    _check(cfg.CM_Get_DevNode_Status(ct.byref(status), ct.byref(problem), node, 0),
           f'CM_Get_DevNode_Status({identity})')
    if status.value & 0x400 or problem.value:
        raise DeviceDetectionError(f'Device driver problem {problem.value}: {identity}')
    return node.value


def _indirect_display_devices():
    # ntddvdeo.h: DEVPKEY_IndirectDisplay contains INDIRECT_DISPLAY_INFO,
    # whose first field is DisplayAdapterLuid. It identifies virtual adapters
    # even when DXGI reports the backing physical GPU's name and PCI IDs.
    cfg = ct.WinDLL('cfgmgr32', use_last_error=True)
    cfg.CM_Get_Device_ID_List_SizeW.argtypes = [ct.POINTER(ct.c_uint32), ct.c_wchar_p, ct.c_uint32]
    cfg.CM_Get_Device_ID_List_SizeW.restype = ct.c_uint32
    cfg.CM_Get_Device_ID_ListW.argtypes = [ct.c_wchar_p, ct.c_void_p, ct.c_uint32, ct.c_uint32]
    cfg.CM_Get_Device_ID_ListW.restype = ct.c_uint32
    cfg.CM_Get_DevNode_PropertyW.argtypes = [ct.c_uint32, ct.POINTER(_PropertyKey), ct.POINTER(ct.c_uint32),
                                          ct.c_void_p, ct.POINTER(ct.c_uint32), ct.c_uint32]
    cfg.CM_Get_DevNode_PropertyW.restype = ct.c_uint32
    display_class = '{4d36e968-e325-11ce-bfc1-08002be10318}'
    length = ct.c_uint32()
    _check(cfg.CM_Get_Device_ID_List_SizeW(ct.byref(length), display_class, 0x300), 'CM_Get_Device_ID_List_SizeW')
    buffer = ct.create_unicode_buffer(length.value)
    _check(cfg.CM_Get_Device_ID_ListW(display_class, buffer, length, 0x300), 'CM_Get_Device_ID_ListW')
    result = {}
    for identity in buffer[:].rstrip('\0').split('\0'):
        if not identity:  # Empty MULTI_SZ denotes no display devices.
            continue
        node = _check_devnode(identity)
        info = _IndirectDisplayInfo()
        size, prop_type = ct.c_uint32(ct.sizeof(info)), ct.c_uint32()
        key = _PropertyKey('c50a3f10-aa5c-4247-b830-d6a6f8eaa310', 1)
        code = cfg.CM_Get_DevNode_PropertyW(node, ct.byref(key), ct.byref(prop_type), ct.byref(info), ct.byref(size), 0)
        if code == 0x25:  # CR_NO_SUCH_VALUE: normal physical adapters have no indirect-display property.
            continue
        _check(code, f'CM_Get_DevNode_PropertyW(IndirectDisplay, {identity})')
        # Windows 10's SDK has the first 20 bytes; newer SDKs append two
        # driver-version ULONGs. Both layouts share the same LUID field.
        if prop_type.value != 0x1003 or size.value not in (20, ct.sizeof(info)):
            raise DeviceDetectionError(f'Invalid INDIRECT_DISPLAY_INFO: {identity}')
        luid = info.DisplayAdapterLuid.key()
        if luid in result:
            raise DeviceDetectionError(f'ambiguous indirect display LUID: {luid}')
        row = {'identity': identity.upper()}
        for field, guid, pid in [('name', 'b725f130-47ef-101a-a5f1-02608c9eebac', 10),
                                 ('driver', 'a8b865dd-2e3d-4094-ad97-e593a70c75d6', 3)]:
            text = ct.create_unicode_buffer(2048)
            size = ct.c_uint32(ct.sizeof(text))
            key = _PropertyKey(guid, pid)
            code = cfg.CM_Get_DevNode_PropertyW(node, ct.byref(key), ct.byref(prop_type), text, ct.byref(size), 0)
            if field == 'driver' and code == 0x25:
                row[field] = None  # Windows explicitly reports no driver-version property.
                continue
            _check(code, f'CM_Get_DevNode_PropertyW({field}, {identity})')
            if prop_type.value != 0x12:
                raise DeviceDetectionError(f'Invalid {field} property type: {identity}')
            row[field] = text.value
        result[luid] = row
    return result


def _enumerate_dxgi():
    # EnumAdapters1 preserves EnumAdapters ordering used by DirectML device_id.
    indirect_devices = _indirect_display_devices()
    dxgi = ct.WinDLL('dxgi', use_last_error=True)
    dxgi.CreateDXGIFactory1.argtypes = [ct.c_void_p, ct.POINTER(ct.c_void_p)]
    dxgi.CreateDXGIFactory1.restype = ct.c_int32
    iid = (ct.c_ubyte * 16).from_buffer_copy(uuid.UUID('770aae78-f26f-4dba-a829-253c83d1b387').bytes_le)
    factory = ct.c_void_p()
    _check(dxgi.CreateDXGIFactory1(ct.byref(iid), ct.byref(factory)), 'CreateDXGIFactory1')
    rows = []
    try:
        index = 0
        while True:
            adapter = ct.c_void_p()
            code = _com(factory, 12, ct.c_int32, ct.c_uint32, ct.POINTER(ct.c_void_p))(
                factory, index, ct.byref(adapter))
            if code & 0xFFFFFFFF == 0x887A0002:  # DXGI_ERROR_NOT_FOUND: end of enumeration.
                break
            _check(code, f'IDXGIFactory1.EnumAdapters1({index})')
            try:
                description = _AdapterDesc1()
                _check(_com(adapter, 10, ct.c_int32, ct.POINTER(_AdapterDesc1))(
                    adapter, ct.byref(description)), f'IDXGIAdapter1.GetDesc1({index})')
                details = ({'identity': None, 'adapter_type': 4, 'driver': None}
                           if description.Flags & 2 else _kernel_details(description.AdapterLuid, indirect_devices))
                rows.append({'name': description.Description, 'dxgi_name': description.Description,
                             'vendor_id': description.VendorId, 'device_id': description.DeviceId,
                             'dedicated_memory_bytes': description.DedicatedVideoMemory, 'dxgi_index': index,
                             'luid': description.AdapterLuid.key(), 'flags': description.Flags, **details})
            finally:
                _com(adapter, 2, ct.c_uint32)(adapter)
            index += 1
    finally:
        _com(factory, 2, ct.c_uint32)(factory)
    return rows


def _cuda_ordinals():
    # CUDA ordinals are independent of DXGI indices. cuDeviceGetLuid is WDDM-only.
    cuda = ct.WinDLL('nvcuda', use_last_error=True)
    cuda.cuInit.argtypes, cuda.cuInit.restype = [ct.c_uint32], ct.c_int32
    cuda.cuDeviceGetCount.argtypes, cuda.cuDeviceGetCount.restype = [ct.POINTER(ct.c_int32)], ct.c_int32
    cuda.cuDeviceGet.argtypes, cuda.cuDeviceGet.restype = [ct.POINTER(ct.c_int32), ct.c_int32], ct.c_int32
    cuda.cuDeviceGetLuid.argtypes = [ct.c_void_p, ct.POINTER(ct.c_uint32), ct.c_int32]
    cuda.cuDeviceGetLuid.restype = ct.c_int32
    _check(cuda.cuInit(0), 'CUDA cuInit')
    count, device, mask = ct.c_int32(), ct.c_int32(), ct.c_uint32()
    _check(cuda.cuDeviceGetCount(ct.byref(count)), 'CUDA cuDeviceGetCount')
    result = {}
    for ordinal in range(count.value):
        _check(cuda.cuDeviceGet(ct.byref(device), ordinal), f'CUDA cuDeviceGet({ordinal})')
        luid = _LUID()
        _check(cuda.cuDeviceGetLuid(ct.byref(luid), ct.byref(mask), device), f'CUDA cuDeviceGetLuid({ordinal})')
        if luid.key() in result:
            raise DeviceDetectionError(f'ambiguous CUDA LUID: {luid.key()}')
        result[luid.key()] = ordinal
    return result


def enumerate_devices() -> list[dict]:
    if sys.platform != 'win32':
        raise DeviceDetectionError('GPU detection supports Windows only')
    try:
        rows = _enumerate_dxgi()
        identities = set()
        for row in rows:
            identity, adapter_type = row['identity'], row['adapter_type']
            if identity:
                if identity.casefold() in identities:
                    raise DeviceDetectionError(f'ambiguous device identity: {identity}')
                identities.add(identity.casefold())
            row['vendor'] = {0x1002: 'amd', 0x10DE: 'nvidia', 0x8086: 'intel'}.get(row['vendor_id'], 'unknown')
            reason = ''
            if row['flags'] & 2 or adapter_type & 4:
                reason = 'software_adapter'
            elif row['flags'] & 1 or adapter_type & (64 | 128):
                reason = 'virtual_adapter'
            elif not adapter_type & 1:
                reason = 'render_unsupported'
            elif not identity:
                raise DeviceDetectionError(f'Missing persistent PnP identity for DXGI index {row["dxgi_index"]}')
            row.update(eligible=not reason, exclusion_reason=reason, cuda_index=None, cuda_error='')
        if any(row['eligible'] and row['vendor'] == 'nvidia' for row in rows):
            try:
                cuda, cuda_error = _cuda_ordinals(), ''
            except (DeviceDetectionError, OSError, AttributeError) as error:
                # DXGI/PnP discovery succeeded; CUDA failure belongs to NVIDIA only.
                cuda, cuda_error = {}, str(error)
            for row in rows:
                if row['vendor'] == 'nvidia':
                    row['cuda_index'] = cuda.get(row['luid'])
                    if row['cuda_index'] is None:
                        row['cuda_error'] = cuda_error or f'CUDA device ordinal could not be resolved: {row["identity"]}'
        return rows
    except (OSError, AttributeError) as error:
        raise DeviceDetectionError(f'Windows GPU detection failed: {error}') from error


def resolve_device(identity: str, backend: str) -> dict:
    """Re-enumerate each worker launch; never replace a lost identity by name/order."""
    if backend not in {'cuda', 'dml'}:
        raise DeviceDetectionError(f'Unsupported GPU backend: {backend!r}')
    if not isinstance(identity, str) or not identity.strip():
        raise DeviceDetectionError('A persistent device identity is required')
    rows = enumerate_devices()
    selected = next((row for row in rows if row['identity'] and row['identity'].casefold() == identity.casefold()), None)
    if selected is None:
        raise DeviceDetectionError(f'Device identity not found: {identity}')
    if not selected['eligible']:
        raise DeviceDetectionError(f'Device excluded: {selected["exclusion_reason"]}: {identity}')
    if backend == 'cuda' and (selected['vendor'] != 'nvidia' or selected['cuda_index'] is None):
        raise DeviceDetectionError(selected['cuda_error'] or f'CUDA device ordinal could not be resolved: {identity}')
    if backend == 'dml' and selected['vendor'] != 'amd':
        raise DeviceDetectionError('DirectML acceleration currently supports AMD hardware only')
    return selected
