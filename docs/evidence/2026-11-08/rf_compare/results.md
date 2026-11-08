| fixture | expected (ON / OFF) | revision | ON: accepted / decisions | OFF: accepted / decisions | false accepts (worst score) | misses | condition |
|---|---|---|---|---|---|---|---|
| spur_only_cold | unknown / unknown | baseline_7997d95 | - | 0/45 {'unknown': 45} | 0 | - | cold start: learning begins on the first frame |
| spur_only_cold | unknown / unknown | pinned_fb9b86a | - | 0/45 {'unknown': 45} | 0 | - | cold start: learning begins on the first frame |
| spur_only_cold | unknown / unknown | fixed | - | 0/45 {'unknown': 45} | 0 | - | cold start: learning begins on the first frame |
| spur_only_settled | unknown / unknown | baseline_7997d95 | - | 0/213 {'unknown': 213} | 0 | - | settled: 240 s of the same frames, background adaptation tau 300 s |
| spur_only_settled | unknown / unknown | pinned_fb9b86a | - | 0/213 {'unknown': 213} | 0 | - | settled: 240 s of the same frames, background adaptation tau 300 s |
| spur_only_settled | unknown / unknown | fixed | - | 0/213 {'unknown': 213} | 0 | - | settled: 240 s of the same frames, background adaptation tau 300 s |
| quiet_no_spur | unknown / unknown | baseline_7997d95 | - | 0/45 {'unknown': 45} | 0 | - | cold start |
| quiet_no_spur | unknown / unknown | pinned_fb9b86a | - | 0/45 {'unknown': 45} | 0 | - | cold start |
| quiet_no_spur | unknown / unknown | fixed | - | 0/45 {'unknown': 45} | 0 | - | cold start |
| fm_offcenter | FM Broadcast / unknown | baseline_7997d95 | 38/38 {'FM Broadcast': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; emitter on from 20 s |
| fm_offcenter | FM Broadcast / unknown | pinned_fb9b86a | 38/38 {'FM Broadcast': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; emitter on from 20 s |
| fm_offcenter | FM Broadcast / unknown | fixed | 38/38 {'FM Broadcast': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; emitter on from 20 s |
| fm_near_lo | FM Broadcast / unknown | baseline_7997d95 | 38/38 {'FM Broadcast': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; emitter on from 20 s, centred on the LO over the spur |
| fm_near_lo | FM Broadcast / unknown | pinned_fb9b86a | 38/38 {'FM Broadcast': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; emitter on from 20 s, centred on the LO over the spur |
| fm_near_lo | FM Broadcast / unknown | fixed | 38/38 {'FM Broadcast': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; emitter on from 20 s, centred on the LO over the spur |
| dab_fullspan | DAB+ / unknown | baseline_7997d95 | 38/38 {'DAB+': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; emitter on from 20 s, fills the whole span |
| dab_fullspan | DAB+ / unknown | pinned_fb9b86a | 38/38 {'DAB+': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; emitter on from 20 s, fills the whole span |
| dab_fullspan | DAB+ / unknown | fixed | 38/38 {'DAB+': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; emitter on from 20 s, fills the whole span |
| cw_offcenter | CW / Beacon / unknown | baseline_7997d95 | 38/38 {'CW / Beacon': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; emitter on from 20 s |
| cw_offcenter | CW / Beacon / unknown | pinned_fb9b86a | 38/38 {'CW / Beacon': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; emitter on from 20 s |
| cw_offcenter | CW / Beacon / unknown | fixed | 38/38 {'CW / Beacon': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; emitter on from 20 s |
| unknown_block_1mhz | unknown / unknown | baseline_7997d95 | 0/38 {'unknown': 38} | 0/7 {'unknown': 7} | 0 | - | cold start; emitter on from 20 s |
| unknown_block_1mhz | unknown / unknown | pinned_fb9b86a | 0/38 {'unknown': 38} | 0/7 {'unknown': 7} | 0 | - | cold start; emitter on from 20 s |
| unknown_block_1mhz | unknown / unknown | fixed | 0/38 {'unknown': 38} | 0/7 {'unknown': 7} | 0 | - | cold start; emitter on from 20 s |
| overlap_fm_cw | CW / Beacon or FM Broadcast / unknown | baseline_7997d95 | 38/38 {'CW / Beacon': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; two emitters on from 20 s, the carrier inside the FM window |
| overlap_fm_cw | CW / Beacon or FM Broadcast / unknown | pinned_fb9b86a | 38/38 {'CW / Beacon': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; two emitters on from 20 s, the carrier inside the FM window |
| overlap_fm_cw | CW / Beacon or FM Broadcast / unknown | fixed | 38/38 {'CW / Beacon': 38} | 0/7 {'unknown': 7} | 0 | 0 | cold start; two emitters on from 20 s, the carrier inside the FM window |
