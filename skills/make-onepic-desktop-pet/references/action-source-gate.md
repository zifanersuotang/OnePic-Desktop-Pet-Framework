# Action Source Gate

Create actions only after the standard character is approved.

For each base action:

1. Start from `candidate/approved.png`, never from a rejected or repaired frame.
2. Describe one action only and generate 2-3 candidates in one versioned batch.
3. Store them under `actions/candidates/<action>/<batch-id>/`.
4. Inspect every candidate at full resolution. Reject the batch on any extra,
   missing, detached, duplicated, or fused limb; unexplained occlusion; fused
   prop; double exposure; dirty silhouette; mixed action; or identity drift.
5. Select one qualified candidate and run:

```powershell
.\.venv\Scripts\python.exe .\tools\pet_workspace.py action-review <pet-id> `
  --action <action> `
  --candidate <candidate-01.png> --candidate <candidate-02.png> `
  --selected <selected.png> `
  --limb-integrity-ok --single-action-ok --identity-ok `
  --no-fused-props --clean-silhouette --yes
```

The command copies the selected frame into `actions/transparent/` and records
candidate, character, and output hashes in `review/action-source-gate.json`.
Changing the approved character or formal action invalidates the gate. Never
manually copy a candidate into the formal action directory.

Mode-specific anatomy:

- Human full body: one pair of arms, two hands, and two legs.
- Human half body: one pair of arms and two hands; every occluded limb must have
  a plausible attachment and direction.
- Animal: preserve the species' limb count and front/rear paw relationships.
- Vehicle seated: distinguish hands, controls, seat, and vehicle parts; reject
  body-panel or steering-control fusion.
