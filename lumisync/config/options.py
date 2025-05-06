from types import SimpleNamespace

# TODO: Should the led option be moved under a different global?
GENERAL = SimpleNamespace(nled=20)

# TODO: Replace the settings.json with this during runtime
# and only use the settings.json on restart?
CONNECTION = SimpleNamespace(
    default=SimpleNamespace(
        multicast="255.255.255.255",
        port=4001,
        listen_port=4002,
        timeout=5,
    ),
    devices=[],
)
# NOTE: Duration is in seconds
AUDIO = SimpleNamespace(sample_rate=48000, duration=0.01)

# TODO: This needs to change as soon as support for multiple devices
# is being implemented -> Similar with next as for the devices query?
COLORS = SimpleNamespace(previous=[], current=[])
