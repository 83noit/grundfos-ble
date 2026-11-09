# GENIbus over Bluetooth LE, as spoken by the Grundfos ALPHA2 GO

What this library implements and how sure we are of each part. "Measured" means read from
a real ALPHA2 GO by this project and checked against the pump's display or a clock. "Answered"
means a real ALPHA2 GO returned plausible values with nothing to check them against. "Captured" means
seen in Grundfos GO app traffic recorded on an ALPHA2 GO 25-90 by the esphome-alpha2go
author (facts only; that project is GPL and none of its code is used here). "Documented"
means described for a related pump (ALPHA HWR = family 52 type 7, ALPHA3, SCALA1) and not yet
confirmed on an ALPHA2 GO. The objects the library reads or writes are defined, with the same
tags, in `grundfos_ble/registry/alpha2go.py`. The QH coefficients (93/180–184, 93/200) and the
clock (94/101) are listed here for reference and are not in the registry.

## Transport

| Item | Value | Status |
|---|---|---|
| Advertised service UUID | 0xFE5D (Bluetooth SIG member UUID of Grundfos A/S) | measured |
| Service data | `03 01 00 fam type ver mode flags tail(2)`; `fam type` = 52 6 for the ALPHA2 GO | measured (flags per SCALA1 docs) |
| Flag bits | 0x10 button pressed (~4 s, pairing window), 0x01 client connected, 0x02 alarm, 0x04 warning, 0x08 LED blinking | 0x10 measured, rest documented |
| GATT characteristic | `859cffd1-036e-432a-aa28-1a0085b87ba9`, write-without-response + notify | measured |
| Write size | ≤ 20 bytes per write, whatever the MTU | captured, documented |
| Reply size | ≤ 20 bytes per notification; reassemble to LEN + 4 | measured |
| Encryption | mandatory; unencrypted link dropped ~2 s after connect; SMP accepted ~3 s after the button; at least two bonds coexist (proxy and phone) | measured |
| While a client is connected | the pump stops advertising; the last advert seen had flag 0x08 | measured |
| Advertising interval | ~8 s, continuous | measured |

## Telegram

```
SD  LEN  DA  SA  APDU…  CRC-hi CRC-lo
```

- SD: 0x27 request, 0x26 message (no reply), 0x24 reply, 0x25 error reply.
- LEN: bytes from DA to the last APDU byte.
- DA/SA: pump 0xE7, client 0xF8 (requests `27 LL E7 F8`, replies `24 LL F8 E7`); broadcast 0xFF.
- CRC-16/CCITT, poly 0x1021, init 0xFFFF, result inverted, big-endian, over LEN..last APDU byte.
- APDU: `class`, `(op << 6) | len`, data (≤ 63 bytes). Request op: 0 GET, 2 SET, 3 INFO.
  Reply ack: 0 OK, 1 class unknown, 2 ID unknown, 3 illegal operation.

Source: GENIbus Protocol Specification (Grundfos, 2005) for everything in this section;
measured on the ALPHA2 GO.

## Classic classes

| Class | Use here | Status |
|---|---|---|
| 2 measured data | IDs 148/149/150 = unit family/type/version (`34 06 00`) | captured; advert bytes agree (measured) |
| 3 commands | 2 reset alarm (SCALA1), 5 stop, 6 start, 7 remote, 8 local, 21 constant flow, 22 constant speed, 23 proportional pressure, 24 constant pressure, 25 min, 26 max | captured (5, 6 tested by others), rest documented |
| 5 reference values | ID 1 `ref_rem` answers 255 = unsupported | captured |
| 7 ASCII strings | 1 product name, 8 product number, 9 serial, 10 production code, 17 unit name, 19 model, 21 GSC file, 50 software, 58 BLE software, 100 ECU id; reply = NUL-terminated string | captured |
| INFO | head byte VI/BO/SIF, UNIT/ZERO/RANGE, Grundfos unit table | spec |

## Class-10 data objects

GET: `0A 03 id sub-hi sub-lo` (several refs may be concatenated). Reply data per object:
`status type(2) version size(3) data(size)`. Replies name the **type**, not the id asked, so
replies are matched by order. SET: `0A (0x80|len) id sub type(2) version size(3) data`;
reply `0A 01 status` (0 OK, 2 busy, 4 failed). Floats are big-endian IEEE-754;
`7F FF FF FF` = not available (and "keep the stored value" in SETs), `FF C0 00 00` = NaN.

| Object | Type | Layout | Status |
|---|---|---|---|
| 93/290 process values | 565 v2, 36 B | flow m³/s @0, Δp Pa @4, N/A @8 @12, media °C @16, ΔT @20, flow m³/h @24, head m @28, "kv" @32 | measured (temps N/A on a heating pump) |
| 87/69 motor state | 256 v3, 41 B | grid V @0, DC-link V @4, A @8, W @12, W @16 (shaft?), rpm @20, NaN @24 @28, electronics °C @32 | measured; @16/@32 meaning unconfirmed |
| 86/8 realised operation | 303, 7 B | source u8, operation mode u8, control mode u8, setpoint f32 (Pa / rpm / m³/s) | captured; answered on the ALPHA2 GO |
| 86/6 local request, 86/10 control-mode request (RW) | 303 | same; SET 86/10 as `00 06 mode 7F FF FF FF` | captured |
| 86/13,15,17,19,21,39 factory limits | 301, 28 B | default, min, max, resulting min, Kp, Ti, Td | captured |
| 86/14,16,18,20,22,40 user setpoint (RW) | 302, 18 B | feedback id u16, setpoint f32, Kp, Ti, Td; write = read-modify-write | captured (20/22 unverified) |
| 86/350 panel setting | 868, 1 B | enum 0–20 | captured; answered on the ALPHA2 GO |
| 86/351–359 panel presets | 869, 5 B | mode u8, setpoint f32 | captured |
| 86/600, 86/601 flow limiters (RW) | 895, 18 B | id, enabled, limit m³/s, Kp, Ti, Td | captured; answered on the ALPHA2 GO |
| 86/620, 86/621 limiter ranges | 897, 9 B | id, lower, upper (m³/s) | captured |
| 90/8 AutoNight (RW) | 333, 1 B | bool | captured; answered on the ALPHA2 GO |
| 90/4 PP curve | 286, 25 B | quadratic u8, foot point, ?, c0, c1, c2, foot point min | captured |
| 93/180–184, 93/200 QH coefficients | 1114 / 563 | floats | captured |
| 88/0 alarms, 88/11 warnings | 570 | uint16 codes, 0-terminated | answered on the ALPHA2 GO (no fault active yet) |
| 93/1 statistics | 248 v2, 28 B | start count u32, starts 1 h u16, starts 24 h u16, operating time s u32, running time 24 h s u32, last cycle s u32, power-ups u32, power-on time s u32 | operating time and last cycle measured; rest answered. Cumulative since commissioning; survives power loss |
| 93/300 temperatures | 534 v2, 13 B | media, PCB, ambient °C (floats), then one byte | answered on the ALPHA2 GO |
| 87/1 energy | 232 v1, 26 B | float64 joules @0; f32 @8, u16 @12, f32 @14, repeated float64 @18 unconfirmed | answered on the ALPHA2 GO |
| 94/101 clock | 322, 12 B | local wall clock | documented (HWR), not implemented |

Error codes follow the GENI ErrorCode enum (`registry/alpha2go.py::ErrorCode`); the ALPHA2 GO
manual's table maps panel codes E1 → 51, E2 → 40, E3 → 4/72/76/85/132, E4 → 57, E9 → 25.

## Measured values (one ALPHA2 GO)

| Date | Flow | Head | Grid | DC-link | Current | Power | Speed | Temp |
|---|---|---|---|---|---|---|---|---|
| 2026-11-07 | 1.427 m³/h | 0.148 m | 235.2 V | 332.7 V | 0.038 A | 4.01 W | 1640 rpm | 44 °C |
| 2026-11-09 | 0.995 m³/h | 0.434 m | 230.6 V | 324.0 V | 0.038 A | 4.01 W | 1658 rpm | 39 °C |

The panel showed 1.4 m³/h, 0.1 m, 4 W and 1640 rpm on 2026-11-07. Frames are in
`src/grundfos_ble/testdata/alpha2go/`.

## Open questions

- Meaning of 93/290 @8, @12 and @32, and of 87/69 @16, @24, @28 on the ALPHA2 GO.
- Which sensor 87/69 @32 reads: it differs from the PCB temperature in 93/300 (42 °C against 37 °C).
- How the alarm objects look with a fault active.
- Whether the alarm/warning/connected advert flag bits behave on the ALPHA2 GO as on the SCALA1.
- How a pump behaves after losing a bond, which has never been observed.
- SMP details (Just Works assumed; IO capability none works through ESPHome proxies).
