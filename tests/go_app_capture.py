# ruff: noqa: E501
"""Frames and object data from captured Grundfos GO traffic on an ALPHA2 GO 25-90.

Source: esphome-alpha2go docs/ble-traffic.md (Birkemosen, 2026-10-28), an HCI snoop of the
GO app. Byte sequences are facts; no code was reused. Duplicates are left out.
"""

from __future__ import annotations

GO_APP_SETS: tuple[tuple[str, str, str], ...] = (
    # (object id/sub-id, what the capture says it wrote, frame)
    ("202/9", "ECU select = 0", "27 11 e7 f8 0a 8d ca 00 09 01 24 01 00 00 04 00 00 00 00 50 1d"),
    ("202/9", "ECU select = 1", "27 11 e7 f8 0a 8d ca 00 09 01 24 01 00 00 04 00 00 00 01 40 3c"),
    (
        "86/10",
        "source 0, operation mode 6, control mode 14 (CP AUTOADAPT), set point NaN",
        "27 14 e7 f8 0a 90 56 00 0a 01 2f 01 00 00 07 00 06 0e 7f ff ff ff 87 c7",
    ),
    (
        "86/21",
        "default 49020, min 24510, max 73530, resulting min 24510, controller 0.1 / 0.95 / 1 (written back unchanged)",
        "27 29 e7 f8 0a a5 56 00 15 01 2d 01 00 00 1c 47 3f 7c 00 46 bf 7c 00 47 8f 9d 00 46 bf 7c 00 3d cc cc cd 3f 73 33 33 3f 80 00 00 a5 89",
    ),
    (
        "86/10",
        "source 0, operation mode 6, control mode 1 (PP), set point NaN",
        "27 14 e7 f8 0a 90 56 00 0a 01 2f 01 00 00 07 00 06 01 7f ff ff ff e2 3e",
    ),
    (
        "86/18",
        "feedback id 4, set point 78432 Pa (8.00 m), controller 0.5 / 8 / 0",
        "27 1f e7 f8 0a 9b 56 00 12 01 2e 01 00 00 12 00 04 47 99 30 00 3f 00 00 00 41 00 00 00 00 00 00 00 99 5f",
    ),
    (
        "86/18",
        "feedback id 4, set point 55882.4 Pa (5.70 m), controller 0.5 / 8 / 0",
        "27 1f e7 f8 0a 9b 56 00 12 01 2e 01 00 00 12 00 04 47 5a 4a 5a 3f 00 00 00 41 00 00 00 00 00 00 00 a4 b7",
    ),
    (
        "86/10",
        "source 0, operation mode 6, control mode 0 (CP), set point NaN",
        "27 14 e7 f8 0a 90 56 00 0a 01 2f 01 00 00 07 00 06 00 7f ff ff ff 48 6f",
    ),
    (
        "86/16",
        "feedback id 4, set point 24510 Pa (2.50 m), controller 0.6 / 1.2 / 0",
        "27 1f e7 f8 0a 9b 56 00 10 01 2e 01 00 00 12 00 04 46 bf 7c 00 3f 19 99 9a 3f 99 99 9a 00 00 00 00 ca ae",
    ),
    (
        "86/16",
        "feedback id 4, set point 73529.4 Pa (7.50 m), controller 0.6 / 1.2 / 0",
        "27 1f e7 f8 0a 9b 56 00 10 01 2e 01 00 00 12 00 04 47 8f 9c b5 3f 19 99 9a 3f 99 99 9a 00 00 00 00 9e a8",
    ),
    (
        "86/16",
        "feedback id 4, set point 39215.7 Pa (4.00 m), controller 0.6 / 1.2 / 0",
        "27 1f e7 f8 0a 9b 56 00 10 01 2e 01 00 00 12 00 04 47 19 2f b0 3f 19 99 9a 3f 99 99 9a 00 00 00 00 93 70",
    ),
    (
        "86/18",
        "feedback id 4, set point 82352.9 Pa (8.40 m), controller 0.5 / 8 / 0",
        "27 1f e7 f8 0a 9b 56 00 12 01 2e 01 00 00 12 00 04 47 a0 d8 78 3f 00 00 00 41 00 00 00 00 00 00 00 a3 4b",
    ),
    (
        "86/10",
        "source 0, operation mode 6, control mode 8 (CF), set point NaN",
        "27 14 e7 f8 0a 90 56 00 0a 01 2f 01 00 00 07 00 06 08 7f ff ff ff 4a 42",
    ),
    (
        "86/40",
        "feedback id 6, set point 0.000277778 m3/s (1.00 m3/h), controller 0.6 / 1.6 / 0.4",
        "27 1f e7 f8 0a 9b 56 00 28 01 2e 01 00 00 12 00 06 39 91 a2 b4 3f 19 99 9a 3f cc cc cd 3e cc cc cd b3 87",
    ),
    (
        "86/10",
        "source 0, operation mode 6, control mode 2 (CC), set point NaN",
        "27 14 e7 f8 0a 90 56 00 0a 01 2f 01 00 00 07 00 06 02 7f ff ff ff 0c ec",
    ),
    (
        "86/14",
        "feedback id 255, set point 5537.2, controller 1 / 1 / 1",
        "27 1f e7 f8 0a 9b 56 00 0e 01 2e 01 00 00 12 00 ff 45 ad 09 9a 3f 80 00 00 3f 80 00 00 3f 80 00 00 bb 5d",
    ),
    (
        "86/16",
        "feedback id 4, set point 49019.6 Pa (5.00 m), controller 0.6 / 1.2 / 0",
        "27 1f e7 f8 0a 9b 56 00 10 01 2e 01 00 00 12 00 04 47 3f 7b 9c 3f 19 99 9a 3f 99 99 9a 00 00 00 00 eb 7d",
    ),
    ("90/8", "enabled 1", "27 0e e7 f8 0a 8a 5a 00 08 01 4d 01 00 00 01 01 e1 dd"),
    ("90/8", "enabled 0", "27 0e e7 f8 0a 8a 5a 00 08 01 4d 01 00 00 01 00 f1 fc"),
    (
        "86/600",
        "limiter 1, enable 1, limit 0.0005556 m3/s (2.00 m3/h), controller 0.5 / 2 / 0",
        "27 1f e7 f8 0a 9b 56 02 58 03 7f 01 00 00 12 01 01 3a 11 a5 af 3f 00 00 00 40 00 00 00 00 00 00 00 cb c8",
    ),
    (
        "86/600",
        "limiter 1, enable 1, limit 0.000833333 m3/s (3.00 m3/h), controller 0.5 / 2 / 0",
        "27 1f e7 f8 0a 9b 56 02 58 03 7f 01 00 00 12 01 01 3a 5a 74 0e 3f 00 00 00 40 00 00 00 00 00 00 00 39 5d",
    ),
    (
        "86/601",
        "limiter 2, enable 1, limit 0.0002778 m3/s (1.00 m3/h), controller 0.5 / 2 / 0",
        "27 1f e7 f8 0a 9b 56 02 59 03 7f 01 00 00 12 02 01 39 91 a5 af 3f 00 00 00 40 00 00 00 00 00 00 00 32 51",
    ),
    (
        "86/601",
        "limiter 2, enable 1, limit 0.000194444 m3/s (0.70 m3/h), controller 0.5 / 2 / 0",
        "27 1f e7 f8 0a 9b 56 02 59 03 7f 01 00 00 12 02 01 39 4b e3 c8 3f 00 00 00 40 00 00 00 00 00 00 00 f5 34",
    ),
    (
        "86/601",
        "limiter 2, enable 0, limit 0.000194444 m3/s (0.70 m3/h), controller 0.5 / 2 / 0",
        "27 1f e7 f8 0a 9b 56 02 59 03 7f 01 00 00 12 02 00 39 4b e3 c8 3f 00 00 00 40 00 00 00 00 00 00 00 e5 d6",
    ),
    (
        "86/600",
        "limiter 1, enable 0, limit 0.000833333 m3/s (3.00 m3/h), controller 0.5 / 2 / 0",
        "27 1f e7 f8 0a 9b 56 02 58 03 7f 01 00 00 12 01 00 3a 5a 74 0e 3f 00 00 00 40 00 00 00 00 00 00 00 29 bf",
    ),
)
"""Every distinct class-10 SET the GO app sent (25)."""

GO_APP_OBJECTS: tuple[tuple[str, int, str, str], ...] = (
    # (object id/sub-id, type, data after the header, the capture's reading)
    (
        "86/10",
        303,
        "00 06 00 7f ff ff ff",
        "source 0, operation mode 6, control mode 0 (CP), set point NaN",
    ),
    (
        "90/4",
        286,
        "00 3f 00 00 00 00 00 00 00 3a d1 c1 52 b2 86 fe 96 28 fc 33 9b 3f 00 00 00",
        "quadratic 0, foot point 0.5, filter 0, coef 0.0016003 / -1.57154e-08 / 2.8e-14, foot point 0.5",
    ),
    (
        "86/6",
        303,
        "00 00 00 46 bf 7c 00",
        "source 0, operation mode 0, control mode 0 (CP), set point 2.50 m",
    ),
    (
        "86/39",
        301,
        "3a 11 a2 b4 38 91 a2 b4 3a 8a 5a 91 38 91 a2 b4 4b 18 96 80 3f 73 33 33 3f 80 00 00",
        "default 0.000555556, min 6.94444e-05, max 0.00105556, resulting min 6.94444e-05, controller 1e+07 / 0.95 / 1",
    ),
    (
        "86/16",
        302,
        "00 04 46 bf 7c 00 3f 19 99 9a 3f 99 99 9a 00 00 00 00",
        "feedback id 4, set point 24510 Pa (2.50 m), controller 0.6 / 1.2 / 0",
    ),
    (
        "86/600",
        895,
        "01 00 3a 11 a5 af 3f 00 00 00 40 00 00 00 00 00 00 00",
        "limiter 1, enable 0, limit 0.0005556 m3/s (2.00 m3/h), controller 0.5 / 2 / 0",
    ),
    (
        "86/14",
        302,
        "00 ff 45 c6 70 00 3f 80 00 00 3f 80 00 00 3f 80 00 00",
        "feedback id 255, set point 6350, controller 1 / 1 / 1",
    ),
    (
        "86/18",
        302,
        "00 04 47 99 30 00 3f 00 00 00 41 00 00 00 00 00 00 00",
        "feedback id 4, set point 78432 Pa (8.00 m), controller 0.5 / 8 / 0",
    ),
    (
        "86/40",
        302,
        "00 06 3a 11 a2 b4 3f 19 99 9a 3f cc cc cd 3e cc cc cd",
        "feedback id 6, set point 0.000555556 m3/s (2.00 m3/h), controller 0.6 / 1.6 / 0.4",
    ),
    (
        "86/13",
        301,
        "45 3b 80 00 44 ce 40 00 45 c6 70 00 c5 c6 70 00 3f 80 00 00 3f 80 00 00 3f 80 00 00",
        "default 3000, min 1650, max 6350, resulting min -6350, controller 1 / 1 / 1",
    ),
    (
        "86/17",
        301,
        "47 08 b8 00 46 65 c8 00 47 a2 c3 00 46 19 30 00 3d cc cc cd 3f 73 33 33 3f 80 00 00",
        "default 35000, min 14706, max 83334, resulting min 9804, controller 0.1 / 0.95 / 1",
    ),
    (
        "86/15",
        301,
        "47 1c 40 00 46 19 30 00 47 a2 c3 00 46 19 30 00 3d cc cc cd 3f 73 33 33 3f 80 00 00",
        "default 40000, min 9804, max 83334, resulting min 9804, controller 0.1 / 0.95 / 1",
    ),
    ("86/351", 869, "02 45 a3 48 00", "control mode 2 (CC), set point 5225 RPM"),
    ("86/352", 869, "02 45 b7 a8 00", "control mode 2 (CC), set point 5877 RPM"),
    ("86/353", 869, "02 45 c6 70 00", "control mode 2 (CC), set point 6350 RPM"),
    ("86/354", 869, "00 46 bf 7c 00", "control mode 0 (CP), set point 24510 Pa (2.50 m)"),
    ("86/355", 869, "00 47 3f 7c 00", "control mode 0 (CP), set point 49020 Pa (5.00 m)"),
    ("86/356", 869, "00 47 8f 9d 00", "control mode 0 (CP), set point 73530 Pa (7.50 m)"),
    ("86/357", 869, "01 47 19 30 00", "control mode 1 (PP), set point 39216 Pa (4.00 m)"),
    ("86/358", 869, "01 47 65 c8 00", "control mode 1 (PP), set point 58824 Pa (6.00 m)"),
    ("86/359", 869, "01 47 99 30 00", "control mode 1 (PP), set point 78432 Pa (8.00 m)"),
    (
        "86/21",
        301,
        "47 3f 7c 00 46 bf 7c 00 47 8f 9d 00 46 bf 7c 00 3d cc cc cd 3f 73 33 33 3f 80 00 00",
        "default 49020, min 24510, max 73530, resulting min 24510, controller 0.1 / 0.95 / 1",
    ),
    (
        "86/19",
        301,
        "47 65 c8 00 47 19 30 00 47 99 30 00 46 19 30 00 3d cc cc cd 3f 73 33 33 3f 80 00 00",
        "default 58824, min 39216, max 78432, resulting min 9804, controller 0.1 / 0.95 / 1",
    ),
    (
        "86/8",
        303,
        "01 00 00 46 bf 7c 00",
        "source 1, operation mode 0, control mode 0 (CP), set point 2.50 m",
    ),
    ("90/8", 333, "00", "enabled 0"),
    (
        "86/601",
        895,
        "02 00 39 91 a5 af 3f 00 00 00 40 00 00 00 00 00 00 00",
        "limiter 2, enable 0, limit 0.0002778 m3/s (1.00 m3/h), controller 0.5 / 2 / 0",
    ),
    ("93/200", 563, "3a 63 43 c4", "coefficient 0.000866946 m3/s"),
    (
        "93/184",
        1114,
        "c5 36 ce 82 3f 27 58 0b 3b 0d 3a be 00 00 00 00",
        "coefficients -2924.91 / 0.653687 / 0.00215499 / 0",
    ),
    (
        "93/180",
        1114,
        "3e c2 6d 20 3a b6 8c 2c b4 c1 97 1a 2f 32 95 30",
        "coefficients 0.379739 / 0.00139273 / -3.6059e-07 / 1.6242e-10",
    ),
    (
        "93/181",
        1114,
        "3e 52 62 e5 ba 27 fe a4 35 8f 7c 34 ad 93 77 20",
        "coefficients 0.205455 / -0.000640849 / 1.06905e-06 / -1.67649e-11",
    ),
    (
        "93/182",
        1114,
        "3f 2e 03 d4 b9 7c d9 70 b3 e1 51 67 2d 7e 97 f6",
        "coefficients 0.679746 / -0.000241136 / -1.04922e-07 / 1.4472e-11",
    ),
    (
        "93/183",
        1114,
        "bf 67 96 89 39 ab cf dc b3 54 6b 3c 2c 0f 97 a5",
        "coefficients -0.904641 / 0.000327705 / -4.94576e-08 / 2.04057e-12",
    ),
    (
        "86/621",
        897,
        "02 38 91 a2 b4 3a 11 a2 b4",
        "limiter 2, lower 6.94444e-05 (0.25 m3/h), upper 0.000555556 (2.00 m3/h)",
    ),
    (
        "86/620",
        897,
        "01 3a 11 a2 b4 3a 8a 5a 91",
        "limiter 1, lower 0.000555556 (2.00 m3/h), upper 0.00105556 (3.80 m3/h)",
    ),
    (
        "86/10",
        303,
        "00 06 0e 7f ff ff ff",
        "source 0, operation mode 6, control mode 14 (CP AUTOADAPT), set point NaN",
    ),
    (
        "86/10",
        303,
        "00 06 01 7f ff ff ff",
        "source 0, operation mode 6, control mode 1 (PP), set point NaN",
    ),
    (
        "86/18",
        302,
        "00 04 47 5a 4a 5a 3f 00 00 00 41 00 00 00 00 00 00 00",
        "feedback id 4, set point 55882.4 Pa (5.70 m), controller 0.5 / 8 / 0",
    ),
    (
        "86/10",
        303,
        "00 06 00 7f ff ff ff",
        "source 0, operation mode 6, control mode 0 (CP), set point NaN",
    ),
    (
        "86/16",
        302,
        "00 04 47 8f 9c b5 3f 19 99 9a 3f 99 99 9a 00 00 00 00",
        "feedback id 4, set point 73529.4 Pa (7.50 m), controller 0.6 / 1.2 / 0",
    ),
    (
        "86/16",
        302,
        "00 04 47 19 2f b0 3f 19 99 9a 3f 99 99 9a 00 00 00 00",
        "feedback id 4, set point 39215.7 Pa (4.00 m), controller 0.6 / 1.2 / 0",
    ),
    (
        "86/10",
        303,
        "00 06 0e 7f ff ff ff",
        "source 0, operation mode 6, control mode 14 (CP AUTOADAPT), set point NaN",
    ),
    (
        "86/10",
        303,
        "00 06 00 7f ff ff ff",
        "source 0, operation mode 6, control mode 0 (CP), set point NaN",
    ),
)
"""Object data the GO app read on connecting (ble-traffic.md, the read table)."""

GO_APP_IDENTITY_REQUEST = "27 07 ff f8 02 03 94 95 96 4f 91"
"""The app's first request: class-2 GET of family, type, version, to the broadcast address."""
GO_APP_IDENTITY_VALUES = "34 06 00"
"""Its answer: family 52, type 6, version 0."""
