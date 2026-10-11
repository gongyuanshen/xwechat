"""NVIDIA acceptance entrypoint; preparation is explicit and no data is downloaded."""
from verify_local_acceleration import main


if __name__ == '__main__':
    main(default_backend='cuda')
