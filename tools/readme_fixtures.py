"""Disposable examples for documentation; never connect to physical devices."""

from lumisync.accounts.tuya import normalize_devices


def example_devices():
    relay = {"switch_1": {"type": "bool", "dp_id": 1}}
    meters = {code: {"dp_id": dp, "values": {"unit": unit, "scale": scale}}
              for code, dp, unit, scale in (("cur_power", 19, "W", 1),
                                           ("cur_voltage", 20, "V", 1),
                                           ("cur_current", 18, "mA", 0),
                                           ("add_ele", 17, "kWh", 3))}
    plugs = []
    for index, name in enumerate(("Example boiler plug", "Example PC power plug", "Example smart plug")):
        plug = normalize_devices({"id": f"example-plug-{index}", "category": "cz", "name": name},
                                 relay, status_functions=meters)[0]
        plug.update(account_id="example-lsc", vendor_account="lsc")
        plugs.append(plug)
    switch = normalize_devices({"id": "example-switch", "category": "kg", "name": "Example wall switch"}, relay)[0]
    switch.update(transport="tuya", ip="192.0.2.2", local_transport="tuya")
    return [
        {"mac": "example-strip", "name": "Example LED strip", "model": "H619C", "sku": "H619C",
         "transport": "lan", "ip": "192.0.2.1"},
        {"mac": "example-matrix", "name": "Example matrix", "transport": "ble",
         "ble_address": "AA:BB:CC:DD:EE:FF", "matrix_size": "32x32"},
        switch, *plugs,
    ]


def example_accounts():
    return [{"id": "example-" + brand, "provider": brand + "_account",
             "label": "Example · demo@example.test", "country_iso": "PL"}
            for brand in ("govee", "tuya", "lsc")]


def example_history(month):
    daily = (2.85, 3.10, 2.95, 3.40, 3.05, 3.65, 3.20, 3.30)
    return {"month": month, "total_kwh": sum(daily), "reported_days": len(daily), "expected_days": len(daily),
            "days": [{"date": f"{month}-{index:02d}", "energy_kwh": value}
                     for index, value in enumerate(daily, 1)]}
