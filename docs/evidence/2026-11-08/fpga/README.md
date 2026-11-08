# FPGA resource appendix (read-only accounting), signalsdrpro build 80fc4042

Requested by `CLAUDE_MO_RF_ACCEPTANCE_DIRECTIVES.md` task 5. Nothing was
synthesized, implemented, modified or programmed: the existing routed
checkpoint of the signalsdrpro Vivado project was opened read-only and
utilization and timing reports were written to a scratch directory, then
copied here. The figures apply to the build identified below. The link
between this build and the image configured on the sensor was established
afterwards by a read-only comparison of the SD card's bitstream file with the
committed `tezuka.xsa` (see "Link to the running image"). The grouped table
was corrected after an independent review (symfront moved from TX to optional
RX processing, the three `interclk*` FIFOs attributed by their connections in
the exported hardware handoff).

## Build identity

| Item | Value |
|---|---|
| HDL project | maia-sdr fork, `maia-hdl/projects/tezuka`, project `signalsdrpro` (checkout `/home/cvalentine/tezuka-live-audit/maia-sdr`, HEAD `cfccfa2f772b80bdd2565d988d4dce7b332f1404`; working tree not clean: `maia-hdl/dvb_fpga` submodule modified, untracked generated IP directories `ip/dvbs2rx`, `ip/iqburst`, `ip/modulator`, `ip/symfront`) |
| Part | `xc7z020clg400-2`, speed grade -2, top `system_top` |
| Implementation run | `signalsdrpro.runs/impl_1`, Vivado 2025.2 (Build 6299465), built 2026-11-07 04:17..04:20 on host radiomancer |
| Routed checkpoint analysed | `impl_1/system_top_routed.dcp`, sha256 `613167cb10d92228705b9e11925a97bb3ffaabab78795bb5c1d01101b70a7e36`, 26475644 bytes |
| Hardware export | `signalsdrpro.sdk/system_top.xsa`, sha256 `80fc4042d17a19d27bdddf8e6e2f33cff8897c0d6367ce16a8155fb163fcc135`; byte-identical to the file committed in this repository as `board/tezuka/signalsdrpro/bitstream/tezuka.xsa` |
| Bitstream inside the xsa | `system_top.bit`, sha256 `be06cf18793f199761ef3ce0fe0f1b10aac6cba786cecf2fc86097e0c172d38a` (this is what `board-fpga` extracts into the firmware image) |
| Report session | Vivado 2025.2 batch, 2026-11-08 14:33, `util.tcl` in this directory; `report_utilization`, `report_utilization -hierarchical -hierarchical_depth 4 -hierarchical_percentages`, `report_timing_summary -max_paths 1` |

## Totals (`util_total.rpt`)

| Resource | Used | Available | Util |
|---|---|---|---|
| Slice LUTs | 29227 | 53200 | 54.94 % |
| of which LUT as logic | 27375 | 53200 | 51.46 % |
| of which LUT as memory | 1852 (578 distributed RAM, 1274 shift register) | 17400 | 10.64 % |
| Slice registers (all flip-flops, no latches) | 35680 | 106400 | 33.53 % |
| F7 / F8 muxes | 924 / 306 | 26600 / 13300 | 3.47 % / 2.30 % |
| Block RAM tiles | 57.5 (46 RAMB36 + 23 RAMB18) | 140 | 41.07 % |
| DSP48E1 | 128 | 220 | 58.18 % |

## Timing

Two numbers exist for this build and both are reported, because they differ:

| Report | WNS | TNS failing endpoints | WHS | THS failing endpoints | WPWS |
|---|---|---|---|---|---|
| This session, from `system_top_routed.dcp` (the post-route checkpoint before the final phys_opt step) | 0.165 ns | 0 | -0.196 ns | 48 | 0.264 ns |
| The build's own final report, `impl_1/system_top_timing_summary_postroute_physopted.rpt` (after post-route phys_opt, the state the bitstream was written from) | 0.165 ns | 0 | 0.035 ns | 0 | 0.264 ns |

The routed checkpoint that was available for a read-only open is the one
written before post-route physical optimisation; the build's final report
shows the 48 hold violations closed. Setup is met in both. Excerpts are in
`timing_summary_excerpt.txt`. Utilization does not change between these two
steps in any material way (phys_opt may replicate a few registers; the
hierarchical table below is from the routed checkpoint).

## Grouped utilization (`util_hier.rpt`, no double counting)

Each block is counted once, in one group, at the depth where it appears as a
block-design cell under `system_i`; the sub-rows of a cell (`inst`, `U0`,
couplers, `xbar`, `rate_gen`, `fir_interpolation_*`) are its own contents and
are not added again. LUTRAM (578) and SRL (1274) are part of the LUT column,
not extra. The group sums reproduce the design totals exactly (LUT 29227,
FF 35680, RAMB36 46, RAMB18 23, DSP 128); the arithmetic check is written
out below the table.

Group membership follows the data-path connections in the exported hardware
handoff (`system.hwh` inside the committed `tezuka.xsa`) and the maia-sdr
block-design scripts, not the utilization report alone. Two corrections
against the first version of this table came out of an independent review:

* `dvbs2_symfront` is an RX block, not a TX block. `common/symfront.tcl`
  inserts it between `mux_decim_i/q` and the `cs12_8mux_chan0/1` RX packing
  path; it contains a mixer, an interpolator, a Gardner timing loop and an
  AGC (`maia_hdl/symfront.py`) and has a bypass mode, which is disabled in
  this build. It is now group B2, optional RX processing (DVB-S2 symbol front
  end). Its 4099 LUT / 22 DSP are a feature decision, not transmit overhead,
  and it sits on the dual-RX sample path: removing or bypassing it changes
  what feeds both `cs12_*` channels.
* The three `interclk*` AXI-stream clock-crossing FIFOs are attributed by
  their connections: `interclk` (`axis_data_fifo`, 55 LUT / 90 FF / 1 RAMB36 /
  1 RAMB18) has `S_AXIS` from `switchdest_M00_AXIS` and `M_AXIS` to
  `util_ad9361_dac_upack`, so it is in the TX path (`common/dvb.tcl`:
  switchdest -> interclk -> upack -> rfifo -> AD9361) and moves to group C.
  `interclk_i` and `interclk_q` (71 LUT / 149 FF each, of which 16 LUTRAM
  each) are connected between `mux_decim_i/q` and `maia_sdr`: they are the RX
  sample clock-crossing FIFOs feeding the spectrometer and move to group B.

Columns are LUT / FF / RAMB36 / RAMB18 / DSP; per-cell figures in the Cells
column are given in the same order, omitting trailing zeros.

| Group | Cells | LUT | FF | RAMB36 | RAMB18 | DSP |
|---|---|---|---|---|---|---|
| A. Transceiver interface (RX and TX, 2R2T) | `axi_ad9361` (ADI core: both RX channels and both TX channels, LVDS interface, decimation/interpolation) 7662/12277/0/0/28 | 7662 | 12277 | 0 | 0 | 28 |
| B. RX capture and spectrum (what the waterfall, classifier and MCP depend on) | `maia_sdr` (spectrometer, DDC, recorder) 3411/3527/21/16/18; `axi_ad9361_adc_dma` 425/665/1; `util_ad9361_adc_fifo` 12/101/1; `util_ad9361_adc_pack` 108/130; dual-RX sample path `cs12_8mux_chan0` 541/461, `cs12_8mux_chan1` 538/493, `cs12_sync_chan0` 55/51, `cs12_sync_chan1` 55/51; RX clock-crossing FIFOs `interclk_i` 71/149, `interclk_q` 71/149 | 5287 | 5777 | 23 | 16 | 18 |
| B2. Optional RX processing: DVB-S2 symbol front end | `dvbs2_symfront` (mixer, interpolator, Gardner timing loop, AGC; bypass mode present, disabled in this build; sits between `mux_decim_i/q` and `cs12_8mux_chan0/1`) 4099/2429/0/3/22 | 4099 | 2429 | 0 | 3 | 22 |
| C. TX and DVB-S2 transmit chain, total | see the two sub-rows | 8162 | 11349 | 19 | 1 | 60 |
| C1. of which DVB-specific processing | `dvbs2_encoder_wrapper_0` 4717/4438/16/0/2; `rrc_2interpol` 746/3124/0/0/38; `switchfir` 671/639; `switchsrc` 507/466; `switchdest` 320/219; `dw_enc_out` 12/107; `dw_rrc_in` 23/102; `dw_rrc_out` 10/102 | 7006 | 9197 | 16 | 0 | 40 |
| C2. of which generic TX transport (retained by any 2R2T transmit support) | `axi_ad9361_dac_dma` 408/486/1; `axi_ad9361_dac_fifo` 21/153/1; `util_ad9361_dac_upack` 114/128; `tx_fir_interpolator` (rate_gen + 2 fir_interpolation) 558/1295/0/0/20; `interclk` (switchdest -> dac_upack clock crossing) 55/90/1/1 | 1156 | 2152 | 3 | 1 | 20 |
| D. Clock and reference control | `vctcxo_lock` 1647/915; `i_adf4001_init` 96/105, `i_adf4001_refctl` 95/80, `i_adf4001_refdet` 63/124 (top level, 254/309 together); top-level glue outside `system_i` 4/6 | 1905 | 1230 | 0 | 0 | 0 |
| E. Interconnect, UART, glue | `axi_gp0_interconnect` 812/686; `axi_hp2_interconnect` 862/1457/3/2; `miniserial` (16550 UART) 337/302; `system_i` minus its cells (PS7 wrapper, resets, glue) 101/173/1/1 | 2112 | 2618 | 4 | 3 | 0 |
| **Total (A + B + B2 + C + D + E)** | | **29227** | **35680** | **46** | **23** | **128** |

Arithmetic check (C is counted once, as C1 + C2):

* arithmetic check: C1 + C2 = 7006 + 1156 = 8162 LUT; 9197 + 2152 = 11349 FF;
  16 + 3 = 19 RAMB36; 0 + 1 = 1 RAMB18; 40 + 20 = 60 DSP = row C.
* arithmetic check: LUT 7662 + 5287 + 4099 + 8162 + 1905 + 2112 = 29227
  (design total 29227).
* arithmetic check: FF 12277 + 5777 + 2429 + 11349 + 1230 + 2618 = 35680
  (design total 35680).
* arithmetic check: RAMB36 0 + 23 + 0 + 19 + 0 + 4 = 46 (design total 46).
* arithmetic check: RAMB18 0 + 16 + 3 + 1 + 0 + 3 = 23 (design total 23).
* arithmetic check: DSP 28 + 18 + 22 + 60 + 0 + 0 = 128 (design total 128).
* glue derivation: `system_i` 28969/35365/46/23/128 minus the sum of its
  29 block-design cells = 101/173/1/1/0 (row E); `system_top` 29227/35680
  minus `system_i` minus the three adf4001 blocks (254/309) = 4/6 (row D;
  2 LUT / 6 FF in `(system_top)` plus 2 LUT in `i_system_wrapper`).

### TX subtotals as shares, two denominators

Block RAM is expressed in tiles, tile = RAMB36 + RAMB18 / 2 (device 140
tiles, design 57.5 tiles). "Device" is the share of the xc7z020 capacity
(53200 LUT, 106400 FF, 140 tiles, 220 DSP); "design" is the share of what
this build uses (29227 LUT, 35680 FF, 57.5 tiles, 128 DSP).

| Subtotal | LUT | FF | BRAM tiles | DSP | LUT % device / design | FF % device / design | BRAM % device / design | DSP % device / design |
|---|---|---|---|---|---|---|---|---|
| C. TX chain, total | 8162 | 11349 | 19.5 | 60 | 15.34 / 27.93 | 10.67 / 31.81 | 13.93 / 33.91 | 27.27 / 46.88 |
| C1. DVB-specific processing | 7006 | 9197 | 16.0 | 40 | 13.17 / 23.97 | 8.64 / 25.78 | 11.43 / 27.83 | 18.18 / 31.25 |
| C2. generic TX transport | 1156 | 2152 | 3.5 | 20 | 2.17 / 3.96 | 2.02 / 6.03 | 2.50 / 6.09 | 9.09 / 15.63 |
| B2. optional RX processing (symfront), for comparison | 4099 | 2429 | 1.5 | 22 | 7.70 / 14.02 | 2.28 / 6.81 | 1.07 / 2.61 | 10.00 / 17.19 |

What these numbers are and are not:

* They are allocations in the routed checkpoint of build 80fc4042, that is,
  what each cell occupies in this placement. They are not a removal estimate.
  Removing or bypassing a block changes synthesis, placement and routing of
  everything around it, and the savings or costs can only be measured on a
  changed build.
* The shared blocks are not removable: `axi_ad9361` (group A) carries both
  RX and TX for both channels and cannot be split at this depth, and the
  AXI interconnects (group E) serve the RX path too. The UART and the glue
  logic in group E are small and were not assessed either way.
* Full 2R2T transmit support would retain all of C2 (DMA, FIFO, unpack,
  interpolator, clock crossing); only C1 is DVB-S2 specific.
* B2 is a feature decision about the RX path, not transmit overhead. It is
  on the dual-RX sample path with a (disabled) bypass; whatever is decided
  about it must keep both `cs12_*` channels fed.

Reading guide:

* Group A cannot be split between RX and TX at this hierarchy depth; the ADI
  `axi_ad9361` core implements both directions and both channels. Preserving
  2R2T means keeping it as it is.
* Group B is the RX dependency of the running sensor's waterfall, classifier
  and MCP evidence. The two `cs12_*` pairs are the per-channel compressed
  sample paths; both channels are present (1189 LUT, 1056 FF for the pair),
  which is the dual-RX capability the directive asks to preserve. The
  `interclk_i/q` pair (142 LUT, 298 FF) is the clock crossing into the
  spectrometer.
* Group C is reported for information only; no removal or change is proposed
  here.

## Link to the running image

Established read-only by the orchestrator on 2026-11-08, with the
qualifications listed at the end of this section.

The sensor (hostname `signalsdrpro`, 192.168.50.25) reports firmware
os-release `VERSION v0.3.24-34-g6e3ab04-dirty` and maia-hdl 0.6.1 via `/api`.
It boots from SD: the kernel command line carries `UBOOT_MODEBOOT=sdboot`.
`/boot/uEnv.txt` sets `bitstream_image=system_top.bin` and

    fpgaload_mmc = "fatload mmc 0 0xF000000 ${bitstream_image}; fpga load 0 0xF000000 ${filesize}"

which is run from the boot command, so the configured bitstream is
`/boot/system_top.bin`.

| Item | Value |
|---|---|
| `/boot/system_top.bin` on the sensor | 2781056 bytes, sha256 `7d249387b5a0e179e2517cb7f7d4e2fc856a834a435fc826f503fa66f6636dc9`, dated Nov 7 10:24; byte-identical to `/boot/tezuka_top.bin` on the same card |
| `system_top.bit` inside the committed `tezuka.xsa` | sha256 `be06cf18793f199761ef3ce0fe0f1b10aac6cba786cecf2fc86097e0c172d38a`; bit header `system_top;COMPRESS=TRUE;Version=2025.2`, part `7z020clg400`, date `2026/11/07 04:20:39` |
| Comparison | after the sync word, all 695248 32-bit configuration words of the two files are identical. The `.bin` is the 32-bit byte-swapped bootgen form of the `.bit` with a 16-byte longer bus-width header, which is why the file hashes differ while the configuration content is the same |
| Locally built firmware image | `output/signalsdrpro/images/sdimg/system_top.bin` (2026-11-07 06:41) has the same sha256 `7d249387...` as the file on the sensor |
| FPGA manager | `/sys/class/fpga_manager/fpga0/state` reads `operating` |
| FPGA version register (read by `S45msd`) | maia-hdl 0.6.1, a generic version number, not a unique identity of this build |

Conclusion: the bitstream file the sensor's boot chain is configured to load
is the bitstream of build 80fc4042, the one analysed in this appendix.

Remaining uncertainty, stated plainly:

* The U-Boot console was not observed. That `/boot/system_top.bin` is the
  file actually loaded is inferred from the configured boot chain
  (`uEnv.txt`, `sdboot`) plus the FPGA manager's `operating` state; the load
  itself was not witnessed.
* The card also holds older bitstreams in `/boot/prev-*` directories (with
  different sizes). They are not the configured image, but they exist.
* The HDL checkout had local modifications (see "Build identity"), so the
  committed source alone does not reconstruct this build; the identity rests
  on the exported `tezuka.xsa` and the bitstream comparison, not on a
  reproducible build.
* The version register reports only maia-hdl 0.6.1, which does not single
  out this build.
* The sensor was rebooted by its owner during the inspection, for reasons
  unrelated to these reads; the inspection figures were re-read afterwards
  where relevant.

## Files

| File | Content |
|---|---|
| `util.tcl` | the read-only script run in Vivado batch mode |
| `design_identity.txt` | part and top from the opened checkpoint |
| `util_total.rpt` | `report_utilization` on the routed checkpoint |
| `util_hier.rpt` | hierarchical utilization, depth 4, with percentages |
| `timing_summary_excerpt.txt` | header and Design Timing Summary tables of both timing reports |
