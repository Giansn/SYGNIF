"""Tests for the eGPU preflight parsers.

The probes themselves shell out to platform tools and cannot run here. The
parsing and classification logic can, and it is where the bugs actually live —
every fixture below is a real-world output shape that breaks a naive parser.
"""

from __future__ import annotations

import unittest
from unittest import mock

from gamedev.realengine.tools import egpu_preflight
from gamedev.realengine.tools.egpu_preflight import (
    FAIL,
    PASS,
    Check,
    Gpu,
    Report,
    UNKNOWN,
    WARN,
    classify_gpu,
    parse_lspci_vga,
    parse_nvidia_smi,
    parse_windows_json,
    summarise_gpus,
)


class TestClassifyGpu(unittest.TestCase):
    def test_discrete_nvidia(self) -> None:
        for name in ("NVIDIA GeForce RTX 4070", "NVIDIA RTX A4000", "GeForce GTX 1080 Ti"):
            vendor, integrated = classify_gpu(name)
            self.assertEqual(vendor, "nvidia", name)
            self.assertFalse(integrated, name)

    def test_intel_integrated(self) -> None:
        for name in ("Intel(R) Iris(R) Xe Graphics", "Intel(R) UHD Graphics 620", "Intel(R) HD Graphics 520"):
            vendor, integrated = classify_gpu(name)
            self.assertEqual(vendor, "intel", name)
            self.assertTrue(integrated, name)

    def test_intel_arc_is_discrete_despite_the_word_graphics(self) -> None:
        # Arc is Intel and discrete. Matching "Intel" plus "Graphics" would
        # misclassify it as integrated and make the preflight report that no
        # eGPU is present while looking straight at one.
        vendor, integrated = classify_gpu("Intel(R) Arc(TM) A770 Graphics")
        self.assertEqual(vendor, "intel")
        self.assertFalse(integrated)

    def test_amd_apu_versus_discrete_radeon(self) -> None:
        _, apu = classify_gpu("AMD Radeon(TM) Graphics")
        self.assertTrue(apu)
        _, vega_apu = classify_gpu("AMD Radeon Vega 8 Graphics")
        self.assertTrue(vega_apu)
        vendor, discrete = classify_gpu("AMD Radeon RX 7900 XTX")
        self.assertEqual(vendor, "amd")
        self.assertFalse(discrete)

    def test_software_renderers_count_as_integrated(self) -> None:
        # llvmpipe means no hardware acceleration at all. Treating it as a
        # discrete GPU would produce a cheerful PASS on a machine that cannot
        # run an engine.
        _, integrated = classify_gpu("llvmpipe (LLVM 15.0.7, 256 bits)")
        self.assertTrue(integrated)
        _, basic = classify_gpu("Microsoft Basic Display Adapter")
        self.assertTrue(basic)

    def test_unknown_vendor(self) -> None:
        vendor, _ = classify_gpu("Some Unreleased Accelerator")
        self.assertEqual(vendor, "unknown")


class TestParseLspci(unittest.TestCase):
    FIXTURE = """\
00:02.0 VGA compatible controller: Intel Corporation Iris Xe Graphics (rev 01)
00:14.0 USB controller: Intel Corporation Tiger Lake-LP USB Controller
0c:00.0 3D controller: NVIDIA Corporation GA104 [GeForce RTX 3070] (rev a1)
0c:00.1 Audio device: NVIDIA Corporation GA104 High Definition Audio Controller
"""

    def test_keeps_only_display_controllers(self) -> None:
        gpus = parse_lspci_vga(self.FIXTURE)
        self.assertEqual(len(gpus), 2)
        self.assertEqual(gpus[0].bus_id, "00:02.0")
        self.assertEqual(gpus[1].bus_id, "0c:00.0")

    def test_strips_revision_suffix(self) -> None:
        gpus = parse_lspci_vga(self.FIXTURE)
        self.assertNotIn("rev", gpus[0].name)
        self.assertTrue(gpus[0].name.endswith("Iris Xe Graphics"))

    def test_classifies_integrated_and_discrete(self) -> None:
        integrated, discrete = parse_lspci_vga(self.FIXTURE)
        self.assertTrue(integrated.integrated)
        self.assertFalse(discrete.integrated)
        self.assertEqual(discrete.vendor, "nvidia")

    def test_empty_input(self) -> None:
        self.assertEqual(parse_lspci_vga(""), [])


class TestParseNvidiaSmi(unittest.TestCase):
    def test_parses_csv_rows(self) -> None:
        fixture = "NVIDIA GeForce RTX 3070, 8192, 550.90.07, 00000000:0C:00.0\n"
        gpus = parse_nvidia_smi(fixture)
        self.assertEqual(len(gpus), 1)
        self.assertEqual(gpus[0].vram_mb, 8192)
        self.assertEqual(gpus[0].driver, "550.90.07")
        self.assertFalse(gpus[0].integrated)

    def test_handles_multiple_gpus(self) -> None:
        fixture = (
            "NVIDIA GeForce RTX 3070, 8192, 550.90.07, 00000000:0C:00.0\n"
            "NVIDIA GeForce GTX 1660, 6144, 550.90.07, 00000000:01:00.0\n"
        )
        self.assertEqual(len(parse_nvidia_smi(fixture)), 2)

    def test_tolerates_missing_memory_value(self) -> None:
        # nvidia-smi prints [N/A] for some fields on some driver versions;
        # crashing the whole preflight over one unreadable field would be worse
        # than reporting the rest.
        gpus = parse_nvidia_smi("NVIDIA RTX A2000, [N/A], 550.90.07, 00000000:0C:00.0\n")
        self.assertEqual(len(gpus), 1)
        self.assertIsNone(gpus[0].vram_mb)

    def test_ignores_blank_lines(self) -> None:
        self.assertEqual(parse_nvidia_smi("\n\n"), [])


class TestParseWindowsJson(unittest.TestCase):
    def test_single_adapter_is_a_bare_object_not_a_list(self) -> None:
        # ConvertTo-Json emits an object rather than an array when there is
        # exactly one adapter -- which is the state a laptop is in *before* the
        # enclosure is attached, so this shape appears precisely when the answer
        # matters most.
        fixture = """
        {"Name":"Intel(R) Iris(R) Xe Graphics","AdapterRAM":1073741824,
         "DriverVersion":"31.0.101.5186","PNPDeviceID":"PCI\\\\VEN_8086",
         "CurrentHorizontalResolution":2880}
        """
        gpus = parse_windows_json(fixture)
        self.assertEqual(len(gpus), 1)
        self.assertEqual(gpus[0].vram_mb, 1024)
        self.assertTrue(gpus[0].driving_display)

    def test_multiple_adapters(self) -> None:
        fixture = """
        [{"Name":"Intel(R) Iris(R) Xe Graphics","AdapterRAM":1073741824,
          "DriverVersion":"31.0.101.5186","PNPDeviceID":"PCI\\\\VEN_8086",
          "CurrentHorizontalResolution":2880},
         {"Name":"NVIDIA GeForce RTX 3070","AdapterRAM":4293918720,
          "DriverVersion":"552.22","PNPDeviceID":"PCI\\\\VEN_10DE",
          "CurrentHorizontalResolution":null}]
        """
        gpus = parse_windows_json(fixture)
        self.assertEqual(len(gpus), 2)
        self.assertTrue(gpus[0].integrated)
        self.assertFalse(gpus[1].integrated)

    def test_adapter_ram_over_4gb_is_reported_as_unknown(self) -> None:
        """The Win32_VideoController quirk worth being careful about.

        AdapterRAM is a 32-bit field, so an 8, 12 or 24 GB card wraps and
        reports 4293918720 -- about 4095 MB. Passing that through would tell
        someone their 24 GB card has 4 GB, which is worse than saying nothing.
        """
        fixture = '[{"Name":"NVIDIA GeForce RTX 4090","AdapterRAM":4293918720,"DriverVersion":"552.22"}]'
        gpus = parse_windows_json(fixture)
        self.assertIsNone(gpus[0].vram_mb)

    def test_small_adapter_ram_is_trusted(self) -> None:
        fixture = '[{"Name":"NVIDIA GeForce GTX 1050","AdapterRAM":2147483648,"DriverVersion":"552.22"}]'
        self.assertEqual(parse_windows_json(fixture)[0].vram_mb, 2048)

    def test_null_resolution_means_not_driving_a_display(self) -> None:
        fixture = '[{"Name":"NVIDIA GeForce RTX 3070","CurrentHorizontalResolution":null}]'
        self.assertFalse(parse_windows_json(fixture)[0].driving_display)

    def test_malformed_json_returns_empty_rather_than_raising(self) -> None:
        self.assertEqual(parse_windows_json("not json at all"), [])
        self.assertEqual(parse_windows_json(""), [])

    def test_entries_without_a_name_are_skipped(self) -> None:
        self.assertEqual(parse_windows_json('[{"AdapterRAM":123}]'), [])


class TestLinuxSysfsHelpers(unittest.TestCase):
    """The Linux DRM path, where an eGPU is meaningfully harder than on Windows."""

    def test_connector_card_extraction(self) -> None:
        self.assertEqual(egpu_preflight.connector_card("card0-DP-1"), "card0")
        self.assertEqual(egpu_preflight.connector_card("card1-HDMI-A-2"), "card1")
        self.assertEqual(egpu_preflight.connector_card("card12-eDP-1"), "card12")

    def test_connector_card_rejects_non_connectors(self) -> None:
        # The card node itself and the render node are not connectors; treating
        # them as such would report a display on every GPU in the machine.
        self.assertIsNone(egpu_preflight.connector_card("card0"))
        self.assertIsNone(egpu_preflight.connector_card("renderD128"))
        self.assertIsNone(egpu_preflight.connector_card("version"))

    def test_pci_normalisation_across_the_three_formats(self) -> None:
        """The bug this prevents is a comparison that silently never matches.

        sysfs writes 0000:0c:00.0, lspci prints 0c:00.0, and nvidia-smi prints
        00000000:0C:00.0. All three name the same device.
        """
        expected = "0c:00.0"
        self.assertEqual(egpu_preflight.normalise_pci("0000:0c:00.0"), expected)
        self.assertEqual(egpu_preflight.normalise_pci("0c:00.0"), expected)
        self.assertEqual(egpu_preflight.normalise_pci("00000000:0C:00.0"), expected)
        self.assertEqual(egpu_preflight.normalise_pci("  0000:0C:00.0  "), expected)

    def test_pci_normalisation_is_idempotent(self) -> None:
        once = egpu_preflight.normalise_pci("0000:0c:00.0")
        self.assertEqual(egpu_preflight.normalise_pci(once), once)

    def test_read_connected_cards_from_a_fake_sysfs(self) -> None:
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as root:
            def connector(name: str, status: str) -> None:
                os.makedirs(os.path.join(root, name))
                with open(os.path.join(root, name, "status"), "w", encoding="utf-8") as handle:
                    handle.write(status + "\n")

            connector("card0-eDP-1", "connected")        # internal panel
            connector("card0-DP-1", "disconnected")
            connector("card1-DP-1", "connected")         # monitor on the eGPU
            connector("card1-HDMI-A-1", "disconnected")
            os.makedirs(os.path.join(root, "renderD128"))  # not a connector

            self.assertEqual(egpu_preflight.read_connected_cards(root), {"card0", "card1"})

    def test_read_connected_cards_tolerates_a_missing_sysfs(self) -> None:
        self.assertEqual(egpu_preflight.read_connected_cards("/nonexistent/drm"), set())

    def test_only_disconnected_outputs_yields_nothing(self) -> None:
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "card0-DP-1"))
            with open(os.path.join(root, "card0-DP-1", "status"), "w", encoding="utf-8") as handle:
                handle.write("disconnected\n")
            self.assertEqual(egpu_preflight.read_connected_cards(root), set())


class TestToolCandidates(unittest.TestCase):
    """Which binary name gets tried, per platform.

    This matters specifically for an eGPU. Under WSL the Windows tools are
    reachable through interop but only as `.exe`, and it is the Windows-side
    nvidia-smi.exe that knows about a card in a Thunderbolt enclosure attached
    to the host. A Linux nvidia-smi inside WSL, where it exists at all,
    describes the virtualised WSL GPU instead -- so looking only for the bare
    name silently reports the wrong device, or none.
    """

    def test_wsl_tries_the_exe_variant(self) -> None:
        with mock.patch.object(egpu_preflight, "detect_platform", return_value="wsl"):
            self.assertEqual(egpu_preflight._candidates("nvidia-smi"), ["nvidia-smi", "nvidia-smi.exe"])

    def test_windows_tries_the_exe_variant(self) -> None:
        with mock.patch.object(egpu_preflight, "detect_platform", return_value="windows"):
            self.assertEqual(egpu_preflight._candidates("vulkaninfo"), ["vulkaninfo", "vulkaninfo.exe"])

    def test_native_linux_does_not(self) -> None:
        with mock.patch.object(egpu_preflight, "detect_platform", return_value="linux"):
            self.assertEqual(egpu_preflight._candidates("lspci"), ["lspci"])

    def test_bare_name_is_preferred_when_both_exist(self) -> None:
        # Order matters: a native tool, if present, should win over interop,
        # which is slower and crosses a process boundary.
        with mock.patch.object(egpu_preflight, "detect_platform", return_value="wsl"):
            self.assertEqual(egpu_preflight._candidates("boltctl")[0], "boltctl")


class TestSummarise(unittest.TestCase):
    def test_splits_discrete_from_integrated(self) -> None:
        gpus = [
            Gpu(name="Intel Iris Xe", integrated=True),
            Gpu(name="RTX 3070", integrated=False),
            Gpu(name="Mystery", integrated=None),
        ]
        discrete, integrated = summarise_gpus(gpus)
        self.assertEqual(len(discrete), 1)
        self.assertEqual(len(integrated), 1)
        # An unclassified adapter belongs to neither, rather than being guessed
        # into one.
        self.assertNotIn("Mystery", [gpu.name for gpu in discrete + integrated])


class TestReportVerdict(unittest.TestCase):
    def test_worst_status_wins(self) -> None:
        report = Report()
        report.add("a", PASS)
        report.add("b", WARN)
        self.assertEqual(report.worst, WARN)
        report.add("c", FAIL)
        self.assertEqual(report.worst, FAIL)

    def test_unknown_ranks_below_warn_but_is_not_pass(self) -> None:
        report = Report()
        report.add("a", PASS)
        report.add("b", UNKNOWN)
        self.assertEqual(report.worst, UNKNOWN)

    def test_all_pass(self) -> None:
        report = Report()
        report.add("a", PASS)
        self.assertEqual(report.worst, PASS)

    def test_check_defaults(self) -> None:
        check = Check("name", PASS)
        self.assertEqual(check.detail, "")
        self.assertEqual(check.remedy, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
