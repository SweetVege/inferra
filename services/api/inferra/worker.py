"""Standalone worker process: `python -m inferra.worker`."""

import logging

from inferra.runtime.queue import work_forever


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).info("Inferra worker started")
    work_forever()


if __name__ == "__main__":
    main()
