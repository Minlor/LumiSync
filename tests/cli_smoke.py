import sys

def main():
    """Smoke-test the CLI `main()` under patched conditions (no network).

    This script patches `lumisync.lumisync.get_data`, `power_on`, and
    `power_off` to be no-ops so the CLI path for `--power` can be exercised
    without touching the network.
    """
    try:
        import lumisync.lumisync as lm

        fake_device = {"ip": "127.0.0.1", "mac": "FA:KE:00:00:00:01"}

        # Patch get_data to return a single device
        lm.get_data = lambda: {"devices": [fake_device], "selectedDevice": 0}

        # Patch power_on / power_off to avoid network
        lm.power_on = lambda device: print("power_on called", device)
        lm.power_off = lambda device: print("power_off called", device)

        # Run --power on
        sys.argv = ["lumisync", "--power", "on"]
        lm.main()

        # Run --power off
        sys.argv = ["lumisync", "--power", "off"]
        lm.main()

        print("CLI smoke tests passed")
    except Exception as e:
        print("CLI smoke test failed:", e)
        raise


if __name__ == "__main__":
    main()
