"""Bounded reads from an operator-owned, digest-addressed material directory."""
import hashlib
import os
import stat

MAX_MATERIAL_BYTES = 256 * 1024 * 1024


class MaterialStore:
    def __init__(self, directory):
        try:
            self.fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ValueError('private material directory required') from exc
        st = os.fstat(self.fd)
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            os.close(self.fd)
            raise ValueError('private material directory required')
        self.checked = set()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.close(self.fd)

    def verify(self, algorithm, digest, label):
        key = (algorithm, digest)
        if key in self.checked:
            return
        try:
            fd = os.open(digest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        except FileNotFoundError as exc:
            raise ValueError('material missing: ' + label) from exc
        except OSError as exc:
            raise ValueError('unsafe material: ' + label) from exc
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MATERIAL_BYTES:
                raise ValueError('unsafe material: ' + label)
            h = hashlib.new(algorithm)
            total = 0
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_MATERIAL_BYTES:
                    raise ValueError('unsafe material: ' + label)
                h.update(chunk)
            if h.hexdigest() != digest:
                raise ValueError('material digest mismatch: ' + label)
        self.checked.add(key)
