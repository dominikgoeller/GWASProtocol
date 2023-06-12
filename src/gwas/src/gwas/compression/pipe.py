# -*- coding: utf-8 -*-
import pickle
from contextlib import AbstractContextManager
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen
from typing import IO, Any

from ..log import logger
from ..utils import unwrap_which

pipe_max_size: int = int(Path("/proc/sys/fs/pipe-max-size").read_text())


class CompressedReader(AbstractContextManager):
    def __init__(self, file_path: Path | str, is_text: bool = True) -> None:
        self.file_path: Path = Path(file_path)
        self.is_text = is_text

        self.process_handle: Popen | None = None
        self.file_handle: IO[str] | None = None

    def __enter__(self) -> IO:
        if self.file_path.suffix in {".vcf", ".txt"}:
            self.file_handle = self.file_path.open(mode="rt")
            return self.file_handle

        decompress_command: list[str] = {
            ".zst": ["zstd", "--long=31", "-c", "-d"],
            ".lrz": ["lrzcat", "--quiet"],
            ".gz": ["bgzip", "-c", "-d"],
            ".xz": ["xzcat"],
            ".bz2": ["bzip2", "-c", "-d"],
        }[self.file_path.suffix]

        executable = unwrap_which(decompress_command[0])
        decompress_command[0] = executable

        bufsize = 1 if self.is_text else -1
        self.process_handle = Popen(
            [*decompress_command, str(self.file_path)],
            stderr=DEVNULL,
            stdin=DEVNULL,
            stdout=PIPE,
            text=self.is_text,
            bufsize=bufsize,
            pipesize=pipe_max_size,
        )

        if self.process_handle.stdout is None:
            raise IOError

        self.file_handle = self.process_handle.stdout
        return self.file_handle

    def __exit__(self, exc_type, value, traceback) -> None:
        if self.process_handle is not None:
            self.process_handle.__exit__(exc_type, value, traceback)
        elif self.file_handle is not None:
            self.file_handle.close()
        self.process_handle = None
        self.file_handle = None


class CompressedBytesReader(CompressedReader):
    def __init__(self, file_path: Path | str) -> None:
        super().__init__(file_path, is_text=False)

    def __enter__(self) -> IO[bytes]:
        return super().__enter__()


class CompressedTextReader(CompressedReader):
    def __init__(self, file_path: Path | str) -> None:
        super().__init__(file_path, is_text=True)


class CompressedWriter(AbstractContextManager):
    def __init__(self, file_path: Path | str, is_text: bool = True) -> None:
        self.file_path: Path = Path(file_path)
        self.is_text = is_text

        self.input_file_handle: IO | None = None
        self.output_file_handle: IO[bytes] | None = None
        self.process_handle: Popen | None = None

    def __enter__(self) -> IO:
        if self.file_path.suffix in {".vcf", ".txt"}:
            self.input_file_handle = self.file_path.open(mode="wt")
            return self.input_file_handle
        else:
            self.output_file_handle = self.file_path.open(mode="wb")

        compress_command: list[str] = {
            ".zst": ["zstd", "-B0", "-T0", "-11"],
            ".lrz": ["lrzip"],
            ".gz": ["bgzip"],
            ".xz": ["xz"],
            ".bz2": ["bzip2", "-c"],
        }[self.file_path.suffix]

        executable = unwrap_which(compress_command[0])
        compress_command[0] = executable

        bufsize = 1 if self.is_text else -1
        self.process_handle = Popen(
            compress_command,
            stderr=DEVNULL,
            stdin=PIPE,
            stdout=self.output_file_handle,
            text=self.is_text,
            bufsize=bufsize,
            pipesize=pipe_max_size,
        )

        if self.process_handle.stdin is None:
            raise IOError

        self.input_file_handle = self.process_handle.stdin
        return self.input_file_handle

    def __exit__(self, exc_type, value, traceback) -> None:
        if self.process_handle is not None:
            self.process_handle.__exit__(exc_type, value, traceback)
        elif self.input_file_handle is not None:
            self.input_file_handle.close()
        self.process_handle = None
        self.input_file_handle = None
        self.output_file_handle = None


class CompressedBytesWriter(CompressedWriter):
    def __init__(self, file_path: Path | str) -> None:
        super().__init__(file_path, is_text=False)

    def __enter__(self) -> IO[bytes]:
        return super().__enter__()


class CompressedTextWriter(CompressedWriter):
    def __init__(self, file_path: Path | str) -> None:
        super().__init__(file_path, is_text=True)


def load_from_cache(cache_path: Path, key: str) -> Any:
    file_path = cache_path / f"{key}.zst"
    if not file_path.is_file():
        return None
    with CompressedBytesReader(file_path) as file_handle:
        try:
            return pickle.load(file_handle)
        except pickle.UnpicklingError as e:
            logger.warning(f'Failed to load "{file_path}"', exc_info=e)
            return None


def save_to_cache(cache_path: Path, key: str, value: Any) -> None:
    with CompressedBytesWriter(cache_path / f"{key}.zst") as file_handle:
        pickle.dump(value, file_handle)