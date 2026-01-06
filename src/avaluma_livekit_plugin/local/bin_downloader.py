import logging
import os
import tarfile
import urllib.request
from pathlib import Path

logger = logging.getLogger("Avaluma Binary Donwloader")


class BinDownloader:
    def __init__(self):
        self.url = "https://storage.googleapis.com/avaluma-public/hvi-bin/linux_x86_py312_cu12.tar.xz"

        self.bin_dir = Path(__file__).parent / "bin"
        if not self.bin_dir.exists() or not any(self.bin_dir.iterdir()):
            logger.debug(f"{self.bin_dir} does not exist or is empty")
            tar_path = self.download_file()
            self.safe_extract_tar(tar_path)
            tar_path.unlink()
        else:
            logger.debug(f"{self.bin_dir} already exists")

    def download_file(self) -> Path:
        dest = Path(__file__).parent / "downloaded_archive.tar.xz"

        # dest.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Downloading {self.url} -> {dest}")
        urllib.request.urlretrieve(self.url, dest)
        return dest

    def safe_extract_tar(
        self, tar_path: Path, extract_to: Path = Path(__file__).parent
    ) -> None:
        extract_to.mkdir(parents=True, exist_ok=True)

        def is_within_directory(base: Path, target: Path) -> bool:
            base = base.resolve()
            target = target.resolve()
            return str(target).startswith(str(base) + os.sep)

        with tarfile.open(tar_path, mode="r:*") as tar:
            # Check every member path before extracting
            for member in tar.getmembers():
                target_path = extract_to / member.name
                if not is_within_directory(extract_to, target_path):
                    raise RuntimeError(f"Unsafe path in archive: {member.name}")

            # All good -> extract
            tar.extractall(path=extract_to, filter="data")

        logger.info(f"Extracted safely to: {extract_to}")


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, "DEBUG"),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    BinDownloader()
