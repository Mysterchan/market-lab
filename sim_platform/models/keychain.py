"""macOS generic-password Security.framework backend; keys never enter argv."""

from __future__ import annotations

import ctypes as C
import sys
from typing import Any, Dict, List, Optional, Tuple, Union, cast

from sim_platform.domain.models import ContractError

from .credentials import MAX_SECRET_LENGTH

# Security.framework status: an item with this service and account already exists.
ERR_SEC_DUPLICATE_ITEM = -25299
# CoreFoundation string encoding constant for UTF-8.
CF_STRING_ENCODING_UTF8 = 0x08000100


class MacOSKeychain:
    """CredentialStore backend over the macOS login keychain (generic passwords).

    Loads CoreFoundation and Security on construction; holds no secrets itself. Each
    call creates and releases its own CoreFoundation objects. Raises ContractError on
    other operating systems.
    """

    # Declared here so type checkers on other systems, where __init__ stops at the
    # platform check, still know the attribute types.
    service: str
    cf: C.CDLL
    sec: C.CDLL

    def __init__(self, service: str = 'io.github.mysterchan.marketlab.models') -> None:
        if sys.platform != 'darwin':
            raise ContractError('macOS keychain is unavailable on this OS')
        self.service = service
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.sec = C.CDLL('/System/Library/Frameworks/Security.framework/Security')
        signatures = {
            'CFStringCreateWithCString': ([C.c_void_p, C.c_char_p, C.c_uint32], C.c_void_p),
            'CFDataCreate': ([C.c_void_p, C.c_void_p, C.c_long], C.c_void_p),
            'CFDictionaryCreate': (
                [
                    C.c_void_p,
                    C.POINTER(C.c_void_p),
                    C.POINTER(C.c_void_p),
                    C.c_long,
                    C.c_void_p,
                    C.c_void_p,
                ],
                C.c_void_p,
            ),
            'CFDataGetLength': ([C.c_void_p], C.c_long),
            'CFDataGetBytePtr': ([C.c_void_p], C.c_void_p),
            'CFRelease': ([C.c_void_p], None),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.cf, name)
            function.argtypes, function.restype = args, result
        for name, args in {
            'SecItemAdd': [C.c_void_p, C.c_void_p],
            'SecItemCopyMatching': [C.c_void_p, C.POINTER(C.c_void_p)],
            'SecItemUpdate': [C.c_void_p, C.c_void_p],
            'SecItemDelete': [C.c_void_p],
        }.items():
            function = getattr(self.sec, name)
            function.argtypes, function.restype = args, C.c_int32

    def _constant(self, name: str) -> Optional[int]:
        """Read a framework constant (a CF object pointer) by its exported name."""
        library = self.cf if name == 'kCFBooleanTrue' else self.sec
        return C.c_void_p.in_dll(library, name).value

    def _query(self, fields: Dict[str, Union[bool, str, bytes]]) -> Tuple[Any, List[Any]]:
        """Build a CFDictionary query; returns it and the CF objects the caller must release."""
        allocations: List[Any] = []
        keys: List[Optional[int]] = []
        values: List[Any] = []
        for name, value in fields.items():
            keys.append(self._constant(name))
            if name == 'kSecClass':
                pointer = self._constant('kSecClassGenericPassword')
            elif name == 'kSecReturnData':
                pointer = self._constant('kCFBooleanTrue')
            elif isinstance(value, bytes):
                pointer = self.cf.CFDataCreate(None, value, len(value))
                allocations.append(pointer)
            else:
                # Every remaining field (service, account) is text.
                pointer = self.cf.CFStringCreateWithCString(
                    None, cast(str, value).encode(), CF_STRING_ENCODING_UTF8
                )
                allocations.append(pointer)
            values.append(pointer)
        query = self.cf.CFDictionaryCreate(
            None,
            (C.c_void_p * len(keys))(*keys),
            (C.c_void_p * len(values))(*values),
            len(keys),
            C.addressof(C.c_byte.in_dll(self.cf, 'kCFTypeDictionaryKeyCallBacks')),
            C.addressof(C.c_byte.in_dll(self.cf, 'kCFTypeDictionaryValueCallBacks')),
        )
        return query, allocations

    def _call(self, method: str, reference: str, *, secret: Optional[str] = None) -> Optional[str]:
        """Run one keychain operation ('get', 'put' or 'delete'); 'get' returns the secret."""
        base: Dict[str, Union[bool, str, bytes]] = dict(
            kSecClass=True, kSecAttrService=self.service, kSecAttrAccount=reference
        )
        if method == 'get':
            base['kSecReturnData'] = True
        if method == 'put':
            # put() always supplies the secret.
            base['kSecValueData'] = cast(str, secret).encode()
        query, allocations = self._query(base)
        result = C.c_void_p()
        try:
            if method == 'get':
                status = self.sec.SecItemCopyMatching(query, C.byref(result))
            elif method == 'delete':
                status = self.sec.SecItemDelete(query)
            else:
                status = self.sec.SecItemAdd(query, None)
                if status == ERR_SEC_DUPLICATE_ITEM:
                    match, owned = self._query(
                        dict(
                            kSecClass=True, kSecAttrService=self.service, kSecAttrAccount=reference
                        )
                    )
                    update, changed = self._query(dict(kSecValueData=cast(str, secret).encode()))
                    try:
                        status = self.sec.SecItemUpdate(match, update)
                    finally:
                        for pointer in [match, update] + owned + changed:
                            self.cf.CFRelease(pointer)
            if status != 0:
                raise ContractError('OS credential operation failed (status ' + str(status) + ')')
            if method == 'get':
                length = self.cf.CFDataGetLength(result)
                if length > MAX_SECRET_LENGTH:
                    raise ContractError('OS credential exceeds supported size')
                return C.string_at(self.cf.CFDataGetBytePtr(result), length).decode()
        finally:
            if result.value:
                self.cf.CFRelease(result)
            for pointer in [query] + allocations:
                self.cf.CFRelease(pointer)
        return None

    def put(self, reference: str, secret: str) -> None:
        self._call('put', reference, secret=secret)

    def get(self, reference: str) -> Optional[str]:
        return self._call('get', reference)

    def delete(self, reference: str) -> None:
        self._call('delete', reference)
