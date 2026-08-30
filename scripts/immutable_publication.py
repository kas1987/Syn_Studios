"""Private no-clobber primitives for immutable repository publications.

The repository root and each publication directory are required to retain
their names while a primitive is active.  In particular, relocating a held
POSIX directory descriptor is outside this helper's bounded threat model.
Path indirection, file replacement, and target-appearance races inside that
stable directory hierarchy fail closed.
"""

from __future__ import annotations

import ctypes
import os
import secrets
import stat
import tempfile
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import BinaryIO


class PublicationSafetyError(RuntimeError):
    """A publication path is indirect, unstable, or outside its repository."""


class _NamedBinaryStream:
    """Give an fd-backed Windows stream the NamedTemporaryFile interface."""

    def __init__(self, stream: BinaryIO, name: Path):
        self._stream = stream
        self.name = str(name)

    def __getattr__(self, name: str):
        return getattr(self._stream, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False


def open_staging_file(
    *, dir: Path, prefix: str, suffix: str, delete: bool = False
):
    """Create a private staging stream that denies replacement and Windows writers."""
    if delete:
        raise ValueError("immutable publication staging requires delete=False")
    directory = Path(dir)
    if os.name != "nt":
        return tempfile.NamedTemporaryFile(
            dir=directory,
            prefix=prefix,
            suffix=suffix,
            delete=False,
        )

    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    invalid_handle = wintypes.HANDLE(-1).value
    handle = invalid_handle
    path: Path | None = None
    for _ in range(128):
        path = directory / f"{prefix}{secrets.token_hex(16)}{suffix}"
        handle = kernel32.CreateFileW(
            str(path),
            0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
            0x00000001,  # FILE_SHARE_READ; deny other writers and deletion.
            None,
            1,  # CREATE_NEW
            0x00000080 | 0x00200000,  # NORMAL | OPEN_REPARSE_POINT
            None,
        )
        if handle != invalid_handle:
            break
        error = ctypes.get_last_error()
        if error not in {80, 183}:  # ERROR_FILE_EXISTS | ERROR_ALREADY_EXISTS
            raise OSError(error, ctypes.FormatError(error), str(path))
    if handle == invalid_handle or path is None:
        raise FileExistsError("could not allocate a unique publication staging path")

    try:
        descriptor = msvcrt.open_osfhandle(
            handle,
            os.O_RDWR | getattr(os, "O_BINARY", 0),
        )
    except BaseException:
        kernel32.CloseHandle(handle)
        path.unlink(missing_ok=True)
        raise
    try:
        stream = os.fdopen(descriptor, "w+b")
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    return _NamedBinaryStream(stream, path)


def is_direct_regular_file(file_stat: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(file_stat, "st_file_attributes", 0)
    return stat.S_ISREG(file_stat.st_mode) and not file_attributes & reparse_flag


def verify_parent(root: Path, target: Path) -> None:
    """Require a target parent to be a direct path beneath a resolved root."""
    root = root.resolve(strict=True)
    parent = Path(os.path.abspath(target.parent))
    try:
        parent.resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise PublicationSafetyError(
            "publication directory must remain within the repository root"
        ) from error

    # Walk the spelling the caller supplied so a symlink or Windows reparse
    # point cannot disappear behind ``resolve()``.  Stop by file identity,
    # rather than lexical prefix, because Windows may report one path through
    # its 8.3 alias (for example RUNNER~1) and the other through its long name.
    current = parent
    while True:
        try:
            current_stat = current.stat(follow_symlinks=False)
        except FileNotFoundError:
            current_stat = None
        except OSError as error:
            raise PublicationSafetyError(
                f"cannot inspect publication directory: {error}"
            ) from error
        if current_stat is not None:
            if not stat.S_ISDIR(current_stat.st_mode) or (
                getattr(current_stat, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                raise PublicationSafetyError(
                    "publication directory must use direct directories within the repository root"
                )
            try:
                if os.path.samefile(current, root):
                    return
            except OSError as error:
                raise PublicationSafetyError(
                    f"cannot compare publication directory identity: {error}"
                ) from error
        if current.parent == current:
            break
        current = current.parent

    raise PublicationSafetyError(
        "publication directory must remain within the repository root"
    )


def read_stable_file(
    root: Path, target: Path, directory_descriptor: int | None = None
) -> bytes | None:
    """Read a contained direct file while rejecting target replacement races."""
    try:
        before = (
            target.stat(follow_symlinks=False)
            if directory_descriptor is None
            else os.stat(
                target.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        )
    except FileNotFoundError:
        return None
    except OSError as error:
        raise PublicationSafetyError(f"cannot inspect publication target: {error}") from error
    if not is_direct_regular_file(before):
        raise PublicationSafetyError("must be a direct regular file")

    try:
        if directory_descriptor is None:
            resolved = target.resolve(strict=True)
            resolved.relative_to(root.resolve(strict=True))
            source = target.open("rb")
        else:
            resolved = target
            source = os.fdopen(
                os.open(
                    target.name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_descriptor,
                ),
                "rb",
            )
        with source:
            opened = os.fstat(source.fileno())
            payload = source.read()
        after = (
            target.stat(follow_symlinks=False)
            if directory_descriptor is None
            else os.stat(
                target.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        )
        if directory_descriptor is None:
            final_resolved = target.resolve(strict=True)
            final_resolved.relative_to(root.resolve(strict=True))
        else:
            final_resolved = target
    except (OSError, RuntimeError, ValueError) as error:
        raise PublicationSafetyError(
            f"changed during publication inspection: {error}"
        ) from error
    if (
        not is_direct_regular_file(opened)
        or not is_direct_regular_file(after)
        or not os.path.samestat(before, opened)
        or not os.path.samestat(opened, after)
        or final_resolved != resolved
    ):
        raise PublicationSafetyError("changed during publication inspection")
    return payload


@contextmanager
def locked_directory(parent: Path):
    """Hold or address a direct directory so replacement cannot redirect I/O."""
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(parent, flags)
        try:
            opened = os.fstat(descriptor)
            current = parent.stat(follow_symlinks=False)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or not stat.S_ISDIR(current.st_mode)
                or not os.path.samestat(opened, current)
            ):
                raise OSError("publication directory changed while it was being locked")
            yield descriptor
        finally:
            os.close(descriptor)
        return

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        str(parent),
        0x80000000,  # GENERIC_READ denies directory rename/delete while held.
        0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error), str(parent))
    try:
        information = FileInformation()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
            error = ctypes.get_last_error()
            raise OSError(error, ctypes.FormatError(error), str(parent))
        current = parent.stat(follow_symlinks=False)
        opened_inode = (information.file_index_high << 32) | information.file_index_low
        if (
            not information.file_attributes & 0x00000010  # FILE_ATTRIBUTE_DIRECTORY
            or information.file_attributes & 0x00000400  # FILE_ATTRIBUTE_REPARSE_POINT
            or current.st_ino != opened_inode
        ):
            raise OSError("publication directory changed while it was being locked")
        yield None
    finally:
        kernel32.CloseHandle(handle)


def hard_link(
    temporary: Path,
    source_descriptor: int,
    target: Path,
    directory_descriptor: int | None,
) -> None:
    """Link the held staging object, never a replacement at its pathname.

    Windows prevents a staging-name replacement while the creating stream is
    open.  Linux instead links through the open descriptor; if the final name
    was unlinked, the descriptor link fails closed rather than publishing a
    replacement object.  Other POSIX implementations without ``/proc`` fail
    transparently instead of falling back to the racy pathname operation.
    """
    opened = os.fstat(source_descriptor)
    try:
        named = temporary.stat(follow_symlinks=False)
    except FileNotFoundError as error:
        raise PublicationSafetyError(
            "publication staging path changed before linking"
        ) from error
    if (
        not is_direct_regular_file(opened)
        or not is_direct_regular_file(named)
        or not os.path.samestat(opened, named)
    ):
        raise PublicationSafetyError(
            "publication staging path changed before linking"
        )

    if directory_descriptor is None:
        os.link(temporary, target)
        return

    descriptor_path = Path(f"/proc/self/fd/{source_descriptor}")
    if not descriptor_path.exists():
        raise PublicationSafetyError(
            "identity-bound hard-link publication requires /proc/self/fd"
        )
    os.link(
        descriptor_path,
        target.name,
        dst_dir_fd=directory_descriptor,
        follow_symlinks=True,
    )


def remove_owned_file(path: Path, reference: os.stat_result) -> bool:
    """Capture a name before deleting it; never unlink a later replacement."""
    try:
        candidate = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not os.path.samestat(reference, candidate):
        return False

    quarantine_name = f".{path.name}.{secrets.token_hex(16)}.rollback"
    quarantine = path.with_name(quarantine_name)
    with locked_directory(path.parent) as directory_descriptor:
        try:
            current = (
                path.stat(follow_symlinks=False)
                if directory_descriptor is None
                else os.stat(
                    path.name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            )
            if not os.path.samestat(reference, current):
                return False
            if directory_descriptor is None:
                path.rename(quarantine)
                captured = quarantine.stat(follow_symlinks=False)
            else:
                os.rename(
                    path.name,
                    quarantine_name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                )
                captured = os.stat(
                    quarantine_name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
        except FileNotFoundError:
            return False

        if os.path.samestat(reference, captured):
            if directory_descriptor is None:
                quarantine.unlink()
            else:
                os.unlink(quarantine_name, dir_fd=directory_descriptor)
            return True

        try:
            if directory_descriptor is None:
                quarantine.rename(path)
            elif stat.S_ISREG(captured.st_mode):
                os.link(
                    quarantine_name,
                    path.name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                os.unlink(quarantine_name, dir_fd=directory_descriptor)
            elif stat.S_ISLNK(captured.st_mode):
                target = os.readlink(quarantine_name, dir_fd=directory_descriptor)
                os.symlink(target, path.name, dir_fd=directory_descriptor)
                os.unlink(quarantine_name, dir_fd=directory_descriptor)
            else:
                raise OSError(f"cannot safely restore captured path {quarantine}")
        except OSError as error:
            raise OSError(
                f"captured concurrent replacement was preserved at {quarantine}: {error}"
            ) from error
        return False
