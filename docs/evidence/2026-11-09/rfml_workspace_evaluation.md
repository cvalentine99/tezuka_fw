# rfml_workspace: can it integrate with the classifier and the RF SOC?

Evaluation of `/home/cvalentine/rfml_workspace` (added by the owner on
2026-11-09) against the tezuka_fw classifier work on branch
`worktree-classifier-mcp-foundation`. Method: four parallel readers over the
workspace and the branch, three independent assessments (near-term host
tooling, a medium-term host service, risks), then one adversarial check per
claim (36 claims, 6 refuted and corrected below). Nothing in the workspace
was modified; the only code run from it was an import check of its venv. Two
claims were then tested on real data from the 2026-11-09 RF session.

## Verdict

Yes, but on the host only, and first as an evaluation tool, not as a
classifier. The workspace cannot run on the sensor, its models do not exist
yet, and its classes are modulation types rather than the service labels an
operator needs. What it can do now, cheaply and without touching the radio,
is generate large, labelled, impaired test signals that the existing replay
harness can score the deployed classifier against. That fills the biggest
gap in the classifier work: there are very few recordings with known
content. A GPU modulation recogniser behind a second read-only MCP tool is a
sound medium-term step once it has its own trusted positives.

## What is in the workspace

| Item | State |
|---|---|
| TorchSig | 2.2.0, clean checkout of TorchDSP/torchsig main at 1197261d (2026-08-31), editable install in `torchsig_env` (Python 3.12.3) |
| PyTorch stack | torch 2.9.1+cu126, torchaudio 2.9.1, torchvision 0.24.1, lightning 2.6.6, numpy 2.3.5, h5py 3.15.1, nvmath-python 0.6.0 (editable) |
| GPU | RTX 4090 24 GB, driver 595.99.02 (CUDA 13.2). Tested 2026-11-09: `torch.cuda.is_available()` is true and TorchSig imports, so the cu126 wheels run on the newer driver |
| Datasets | two small demo sets in `datasets/torchsig-2.2`: 50 IQ items (4096 samples, one signal each) and 100 spectrogram items (1 to 5 signals, level-2 impairments, boxes with start/stop/lower/upper frequency and SNR); 68 class names |
| sig53_train | only two YAML descriptors; the 479 GB `data.h5` named in `TORCHSIG_2.0_FIXES.md` is gone, as is the documented backup directory |
| Models | none. TorchSig 2.2.0 ships no model package, no pretrained weights and no ONNX/TorchScript export; the example notebooks train from scratch |
| Disk | 34 GB free (97 % full): train from the iterable dataset on the fly, do not write large datasets |

## What does not fit

* **Nothing runs on the SignalSDR Pro.** Every compiled wheel is x86_64 and
  CUDA; no tezuka defconfig builds Python. The on-board classifier is C with
  NEON. Only data products (templates, gates, thresholds) or a small model
  hand-ported to C could ever reach the board, and the latter has not been
  evaluated.
* **The classifier never sees IQ.** It reads maia-httpd `/waterfall` frames:
  4096 bins of integrated linear power, step word in float 0. TorchSig
  models take IQ as a (2, N) array or dB spectrograms. Correction from the
  adversarial pass: TorchSig spectrograms are Blackman-windowed (dsp.py line
  1077), despite a docstring that says no window is applied; they are in dB
  with a floor 100 dB below the peak and frequency descending on axis 0.
* **The labels answer a different question.** TorchSig's classes are
  modulations and protocol builders (fm, ofdm-N, psk, qam, lora, adsb,
  dvbs2 ...). The board's templates are services: FM Broadcast (400 kHz),
  DAB+ (2.5 MHz), CW / Beacon (30 kHz). There is no jammer, rogue-emitter or
  protected-band class, and there cannot be: those are judgements about
  intent and authorisation that a modulation label does not carry. TorchSig's
  protocol builders also skip FEC and framing, so no synthetic signal is a
  real FM station with pilot and RDS or a real DAB+ ensemble.
* **Synthetic data is not ground truth.** A TorchSig `fm` signal never
  satisfies the trusted-positive rule of the capture plan; it is development
  data only.

## Tested today on real sensor data

1. **The sensor's IQ recorder feeds the host directly.** `GET /recording`
   returns a SigMF archive (`ci16_le`, 12-bit samples, centre frequency,
   rate and start time in the metadata). A 10 s recording at 2.5 MS/s is
   100 MB. This is the IQ path the assessments listed as unverified; it is
   the natural input for TorchSig's SigMF reader and for a host GPU model.
2. **IQ to sensor-PSD bridge.** Recomputing the sensor's frames from that IQ
   (4096-point windowed FFT, fftshift, 610 integrated FFTs per frame) and
   aligning them with the frames the sensor's own spectrometer produced in
   the same seconds gives a per-frame dB-shape correlation of 0.985 (worst
   frame 0.914) for any tapered window, against 0.958 without a window. The
   level offset is about -38 dB with Blackman-Harris but varies by frame
   (-36.0 to -40.7 dB), and the pooled residual spread is 2.6 dB. At manual
   71 dB the ADC clipped intermittently on this band (KIGL, 100 kW, about
   5 km away), and the frames where either path clipped are the outliers
   (4.75 dB in the worst); frames without clipping still differ by 0.8 to
   2.2 dB. So the bridge reproduces the sensor's PSD shape, but not yet its
   levels frame for frame: the spectrometer's window, frame phase and
   scaling still have to be pinned down, on a recording without clipping.
3. **Ground truth from the same IQ.** FM demodulation plus RDS decoding of
   that recording identified the positive (KIGL, PI 0x25C7) independently of
   the classifier. The same path gives any future ML model its labels for
   broadcast FM.

## Integration paths, ranked by value per effort

1. **Synthetic emitters for the replay harness (do first).** Generate
   wideband IQ with 1 to 5 non-target signals from TorchSig (btle, zigbee,
   lora, 80211a, adsb, gsm, dmr, p25), apply impairments, convert to sensor
   frames with the bridge above, and inject them into recorded noise floors
   the way `inject_emitter.py` does, or write standalone `.raw` files for
   `rf_compare.py --recorded`. Every accept on them is `confirmed_false` by
   construction. One new host script; no change to the daemon or the radio.
   The bridge must use the Average-mode scaling so the SNR and prominence
   gates see realistic levels.
2. **SNR-swept positives for threshold evidence.** The fm and ofdm builders
   at the template widths, swept over 0 to 50 dB SNR, give a detection
   curve per template for the current threshold and gates. Output is a
   recommendation that must be confirmed on real captures (S1, S1b, S1c of
   2026-11-09, `rf-session/`) before any threshold changes. That session also
   showed the live mask history matters more than the threshold: a synthetic
   test set must be replayed both cold and chained after retunes.
3. **Impairment-aware templates.** Average the bridge PSD of a few hundred
   impaired realizations into an SCFT v3 template with a measured feature
   gate, installed through the existing validated `cmd/classifier/templates`
   path. Lower value: templates from real stations remain the better source.
4. **Host GPU recogniser behind a second read-only MCP tool (medium term).**
   Input: SigMF recordings pulled from the sensor by the operator (the MCP
   server never starts a recording). Train from scratch on the 4090 with
   on-the-fly TorchSig data plus the session's labelled recordings. Output
   per finding: capture id and hash, UTC span, LO/span/gain with the
   `bound` flag, model id and weights hash, class names taken from the
   dataset's own `dataset_info.yaml` (class order differs between generated
   sets), top-k scores, age, and a disposition that defaults to
   `unverified`.
5. **Not now:** live IQ streaming inference (link bandwidth at 30.72 MS/s is
   about 123 MB/s, and recorder downloads coincided with classifier
   anomalies on 2026-11-09, still under investigation); on-device ML; and
   anything built on the missing sig53 data.

## Risks for the SOC, and the evidence an ML label needs

* An agent that adds a modulation name, or words such as "jammer" or
  "rogue", to a template match turns an `unverified` accept into a confident
  operator-facing claim. The MCP now separates the matched window from the
  whole-span residual peak for the same reason; any new tool must keep that
  discipline.
* Before an ML label reaches an operator: trusted positives on this
  receiver (RDS, DAB+ labels, a second receiver or a published list plus
  reception), the development/evaluation split fixed before results, false
  accepts reported as confirmed, suspected and unverified separately, class
  names read from the dataset, and every label carrying its disposition,
  scope and an experimental flag. A label is never promoted to an SOC
  category without a separate rule source.

## Recommended next steps

1. Write the TorchSig-to-sensor-frame bridge as a host script with a test
   against today's P0 IQ and frames (target: shape correlation of 0.98 or
   better on clean frames).
2. Build the confirmed-false negative set from it and replay it against the
   deployed binary (f87a3c46) with `rf_compare.py`.
3. Only after the 2026-11-09 evaluation captures are replayed, propose any
   threshold or template change, citing both the synthetic curves and the
   real captures.
