# BOX-3 table observations

BOX-3 stayed face-up on the same table as a plastic bottle landing spot. This
dataset supports a simulated impact demo. It does not validate human-fall
detection. Nobody fell for these recordings.

| File | Contents |
| --- | --- |
| `demo_bottle_drop_only.csv` | 19 clean trials, 2,500 samples; all pass the recorder's completeness check |
| `demo_bottle_box_fall.csv.excluded` | Overall trial 6, fifth drop in the batch; operator reported the bottle knocked BOX-3 off the table |
| `demo_bottle_impact.csv.excluded` | Initial 170-sample attempt included a pickup, confirmed by the operator |
| `demo_table_settings.json` | Provisional table-demo settings, selection method and replay results |

Excluded files keep their raw samples but are outside the loader's `*.csv`
pattern. The Pi also retains the original combined 20-trial file. Clean and
excluded box-fall rows were checked to reconstruct that original exactly.

The first clean trial has 310 samples. The subsequent batch captured two
seconds of `DROP NOW` and four seconds of `LEAVE STILL` per trial. `PICK UP`
and `READY` setup periods were outside the saved windows. The one-off driver
remains on the Pi at `edge/firsthour_guided_bottle.py`; it requires exactly one
existing trial and cannot rerun against the completed dataset. The operator
requested no further loud tests after the batch.

## Provisional demo settings

Clean acceleration-change peaks range from 0.0727 to 1.1639 g, with median
0.3659 g. All drop microphone peaks clip at 32768. For each trial, a still tail
starts two seconds after its largest acceleration change. Across these tails,
the largest sound peak is 4436 and largest acceleration change is 0.0271 g.

The midpoint between the largest still-tail value and smallest drop peak gives
`SOUND_THRESHOLD=18602` and `ACCEL_THRESHOLD_G=0.0499`. Replaying the samples
through `Fusion` with the existing 400 ms agreement window triggers 19 of 19
recorded drops and zero of 19 still tails. The 30-second cooldown is unchanged.

These values are loaded only into the Pi's private root `.env`. Repository
defaults remain unchanged. The calibration analyzer correctly treats
`demo_bottle_drop_only` as a non-fall label; do not use this dataset alone with
`analyze --write` to select human-fall thresholds.

The replay uses the recordings that selected the values. Still tails are not
independent non-fall controls. Claps, footsteps, gentle placements and other
room activity have not been checked with these settings. No physical drop was
run after tuning, and the microphone clipping limits sound comparisons. The
full calibration and false-alarm tasks remain open.
