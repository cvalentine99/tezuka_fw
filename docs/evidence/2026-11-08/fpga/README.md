# FPGA resource appendix (read-only accounting), signalsdrpro build 80fc4042

Requested by `CLAUDE_MO_RF_ACCEPTANCE_DIRECTIVES.md` task 5. Nothing was
synthesized, implemented, modified or programmed: the existing routed
checkpoint of the signalsdrpro Vivado project was opened read-only and
utilization and timing reports were written to a scratch directory, then
copied here. The figures apply to the build identified below. They are NOT
linked to the image running on the sensor: the sensor was not asked for its
bitstream identity in this pass (see "Link to the running image").

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
are not added again. The group sums reproduce the design totals exactly
(LUT 29227, FF 35680, RAMB36 46, RAMB18 23, DSP 128).

| Group | Cells | LUT | FF | RAMB36 | RAMB18 | DSP |
|---|---|---|---|---|---|---|
| A. Transceiver interface (RX and TX, 2R2T) | `axi_ad9361` (ADI core: both RX channels and both TX channels, LVDS interface, decimation/interpolation) | 7662 | 12277 | 0 | 0 | 28 |
| B. RX capture and spectrum path (what the waterfall, classifier and MCP depend on) | `maia_sdr` (spectrometer, DDC, recorder) 3411/3527/21/16/18; `axi_ad9361_adc_dma` 425/665/1; `util_ad9361_adc_fifo` 12/101/1; `util_ad9361_adc_pack` 108/130; **dual-RX sample path** `cs12_8mux_chan0` 541/461, `cs12_8mux_chan1` 538/493, `cs12_sync_chan0` 55/51, `cs12_sync_chan1` 55/51 | 5145 | 5479 | 23 | 16 | 18 |
| C. TX and DVB-S2 (not used by the RX sensor function) | `dvbs2_encoder_wrapper_0` 4717/4438/16/0/2; `dvbs2_symfront` 4099/2429/0/3/22; `rrc_2interpol` 746/3124/0/0/38; `tx_fir_interpolator` 558/1295/0/0/20; `switchfir` 671/639; `switchsrc` 507/466; `switchdest` 320/219; `axi_ad9361_dac_dma` 408/486/1; `axi_ad9361_dac_fifo` 21/153/1; `util_ad9361_dac_upack` 114/128; `dw_enc_out`, `dw_rrc_in`, `dw_rrc_out` 45/311 | 12206 | 13688 | 18 | 3 | 82 |
| D. Clock and reference control | `vctcxo_lock` 1647/915; `i_adf4001_init`, `i_adf4001_refctl`, `i_adf4001_refdet` (top level) 254/309; top-level glue 4/6 | 1905 | 1230 | 0 | 0 | 0 |
| E. Interconnect, UART, clock-crossing FIFOs, glue | `axi_gp0_interconnect` 812/686; `axi_hp2_interconnect` 862/1457/3/2; `miniserial` (16550 UART) 337/302; `interclk`, `interclk_i`, `interclk_q` (AXI-stream clock-crossing FIFOs) 197/388/1/1; PS7 wrapper, resets and glue not in any cell 101/173/1/1 | 2309 | 3006 | 5 | 4 | 0 |
| **Total** | | **29227** | **35680** | **46** | **23** | **128** |

Reading guide:

* Group A cannot be split between RX and TX at this hierarchy depth; the ADI
  `axi_ad9361` core implements both directions and both channels. Preserving
  2R2T means keeping it as it is.
* Group B is the RX dependency of the running sensor's waterfall, classifier
  and MCP evidence. The two `cs12_*` pairs are the per-channel compressed
  sample paths; both channels are present (1189 LUT, 1056 FF for the pair),
  which is the dual-RX capability the directive asks to preserve.
* Group C is 41.8 % of the LUTs, 38.4 % of the flip-flops, 64 % of the DSPs
  and 18 of the 46 RAMB36 of the design: the DVB-S2 transmit chain. It is
  reported for information only; no removal or change is proposed here.
* `interclk_*` are attributed to group E because their data direction is not
  determinable from the utilization report alone; they are small.

## Link to the running image

Not established in this pass. The repository `tezuka.xsa` for signalsdrpro
equals this build's export byte for byte, and the firmware's `board-fpga`
package extracts `system_top.bit` from it, so a firmware built from this
revision carries bitstream `be06cf18...`. Whether the sensor at 192.168.50.25
is running that bitstream was not checked (that needs either the firmware
version's build manifest or a read of the device's FPGA identity, which was
out of scope for a read-only pass that must not disturb the sensor). The
sensor reports firmware `tezuka-v0.3.24-34-g6e3a`, maia-hdl 0.6.1.

## Files

| File | Content |
|---|---|
| `util.tcl` | the read-only script run in Vivado batch mode |
| `design_identity.txt` | part and top from the opened checkpoint |
| `util_total.rpt` | `report_utilization` on the routed checkpoint |
| `util_hier.rpt` | hierarchical utilization, depth 4, with percentages |
| `timing_summary_excerpt.txt` | header and Design Timing Summary tables of both timing reports |
