---
name: make-onepic-desktop-pet
description: Create, revise, migrate, validate, and release a Windows desktop pet with the OnePic-Desktop-Pet-Framework. Use for a new image/video pet, character or walk approval, 40-frame upgraded-base assembly, runtime movement-region control, default double-click compact chat UI, advanced multi-frame and micro-expression customization, optional side-walk variants and local dialogue, opt-in directional gaze, source-only Live2D integration research, QA repair, legacy migration, or a privacy-safe complete-directory Windows ZIP.
---

# Make OnePic Desktop Pet

Use the existing project and per-pet workspace. Do not create a replacement app,
duplicate skill, or character-specific production script.

Default to the upgraded sprite route unless the user explicitly asks for
Live2D, Cubism, or a hybrid version. The default is the declarative 38-frame
base plus two touch-transition frames for every new pet; do not generate,
enable, or package Live2D implicitly. An explicit hybrid request still builds
and approves the complete sprite route first, then adds Live2D for idle only.

Treat product tiers independently:

- Ordinary requests use the 40-frame upgraded base only.
- Add the advanced multi-frame pack only after the user explicitly requests an
  advanced version, extension actions, or named capabilities. Read
  [references/advanced-customization.md](references/advanced-customization.md).
- The reusable advanced example is 65 frames: 40 upgraded-base frames plus
  signature, focus, jump, waiting-response, and six micro-expression frames.
  An explicitly selected eight-frame side walk makes 73 while preserving the
  approved front walk as the default.
- Enable the offline dialogue pack only after the user explicitly asks for
  chat bubbles, random lines, or custom dialogue. It adds no image frames and
  must remain disabled in the base template.
- Directional/8-way mouse gaze is a separate opt-in. Never infer it from a
  reference, another pet, or an advanced-version request; keep
  `directional_mouse_follow.enabled: false` unless the user names it.

Every Windows base and advanced pet inherits the runtime movement-region menu.
Keep `movement_region_enabled: false` and `movement_region: null` in shared
defaults; never bake producer-machine screen coordinates into a pet manifest or
share package. The user's local toggle and `x, y, width, height` rectangle apply
only to autonomous walking and cursor chase. Manual dragging ignores that custom
rectangle while retaining the visible-screen safety boundary. This is a zero-frame
runtime preference and requires no character, walk, or extension approval.

Every Windows pet inherits `companion_ui.double_click_chat: true`: double-click
opens the compact chat page and replies use the crystal text bubble. This UI
entry is independent of the optional offline dialogue pack and does not enable
networking; opening it must make no request, and cloud chat remains off until
the user configures it and clicks Send. Disable the entry only when the user
explicitly asks.

## Start

1. Read project `AGENTS.md`, `文件夹说明.md`, and `agent-guide/AGENT_GUIDE.md`.
2. Run `scripts/check_environment.ps1`, then
   `scripts/setup_environment.ps1` if `.venv` is missing; install only project
   dependencies in `.venv`.
3. Read [references/workflow.md](references/workflow.md).
4. Create a unique workspace with `scripts/new_pet.ps1`; select one of
   `full-body`, `half-body`, `animal`, `vehicle-seated`, or `static`.
5. Keep all private inputs under `user_assets/pets/<pet-id>/`.

For an existing workspace, and again after every mutation, approval, failure,
or resumed turn, run:

```powershell
.\.venv\Scripts\python.exe .\tools\pet_workspace.py agent-status <pet-id>
```

Treat its `decision` as the single allowed next production action, then observe
again. If `must_pause` is true, stop for the named user input or approval. The
snapshot is read-only and cannot replace full-resolution visual review. Read
[references/agent-loop.md](references/agent-loop.md) for the control contract,
stable blocker categories, and recovery rules. After presenting current
evidence, use the matching `decision` field only when the user explicitly makes
that decision. A non-null `command`, `approval_command`, `rejection_command`, or
`recovery_command` without placeholders is executable exactly as emitted.
Never execute `command_template`, `rejection_command_template`, or
`recovery_command_template` literally: collect only the values named by
`required_inputs`, safely escape each as one argument, render the template, and
do not invent extra inputs. Reobserve immediately afterward.

For video, use a frame extracted from the original video as `source/selfie.png`.
For fictional or legacy generated characters with no original photo, set
`selfie_required` to `false`; do not invent a source selfie. Do not silently
install a system decoder.

## Gates

Generate exactly one standard character candidate. Preserve identity and style.
Register it with `onepic_workflow.py --pet-id <id> character-candidate`, show it,
and pause. Record approval only after direct user confirmation.

After approval, create one action at a time. For each of the eight transparent
actions, generate only 2-3 candidates under
`actions/candidates/<action>/<batch-id>/`, using the approved character as the
only identity anchor. Never continue editing a frame with extra or missing
limbs, double exposure, or fused props; restart that action from the approved
character. Inspect every candidate for limb integrity, one clear action,
identity consistency, unfused props, and a clean silhouette. Record the chosen
frame with `pet_workspace.py action-review`; only that command may copy it into
`actions/transparent/`. Read
[references/action-source-gate.md](references/action-source-gate.md).

After all eight action-source gates pass, create the eight physical walk
phases. For every newly initialized pet, also create `touch_soften.png` and
`touch_return.png`; migrated pets may omit them and use the runtime blend
fallback. Generate and show the walk GIF, then pause again. Static or vehicle
projects without an eight-phase walk must submit an alternative preview with
the rendered `command_template` after supplying its declared `preview_path`.
Reobserve, present that preview, then use only the exact approval command or
the rejection template rendered with the declared user feedback; its CAS binds both
the preview hash and interaction mode. A rejection records the user's feedback;
revise from the approved character and submit a new preview instead of changing
status directly. `full-body` and `animal` are walk-only. If both walk and
interaction are approved, validation and the minimal release workflow are
walk-first; interaction is only a no-walk fallback for an allowed pet mode.

A migrated pet that declares dedicated touch transitions but lacks current
provenance is different from a true zero-transition fallback. Run the reported
`touch-transitions` action: it deterministically rebuilds from approved
sources, backs up existing transition files, and requires visual review of the
new runtime. Never silently adopt the unprovenanced files.

Approvals live only in `user_assets/pets/<pet-id>/workflow.json` and must remain
bound to the current material hashes.

For approved advanced work, keep every revision in a versioned directory.
Create a GIF, contact sheet, and hash report; pause for direct confirmation.
Different extension batches may be approved separately, but activation must
find a matching approved frame-bundle hash for every currently configured item.
Treat the six-frame micro-expression set as one atomic bundle. For a side-walk
request, first approve one right-facing side model, then generate and separately
approve its eight physical phases; never replace or disable the front walk.
For local dialogue, show a real bubble preview and bind approval to the
normalized semantic configuration hash even though it adds zero image frames.

Read [references/asset-contract.md](references/asset-contract.md) before actions.
Do not build when `review/action-source-gate.json` is absent, incomplete, bound
to another character hash, or stale relative to a formal action PNG.
Read [references/configuration.md](references/configuration.md) for the
declarative 38-frame base recipe, 0–2 default touch-transition slots, modes,
alpha-mass contract, and touch behavior.
Only after an explicit Live2D/Cubism/hybrid request, select
`rendering_mode=hybrid-live2d` and read
[references/live2d-hybrid.md](references/live2d-hybrid.md). Otherwise keep
`rendering_mode=sprite` and do not load that variant.

## Build and release

After both gates:

```powershell
.\.venv\Scripts\python.exe .\tools\pet_workspace.py validate <pet-id>
.\scripts\build_pet.ps1 -PetId <pet-id> -Activate
```

Inspect all artifacts listed in
[references/qa-checklist.md](references/qa-checklist.md). Build failures must
leave the last runtime active and quarantine staging; inspect `failed-builds/`
and do not rerun the same build blindly. Only after diagnosing a transient
failure, execute an exact `decision.recovery_command`, or safely render
`recovery_command_template` with its declared `diagnostic_reason`, to archive
the diagnosis and permit one retry. Do not roll back an unchanged active runtime.
Use `scripts/rollback_pet.ps1`
only after a successful activation is later rejected, and first verify that
`user_assets/active-pet.json` names the intended pet.

After presenting the current build's GIFs, contact sheet, QA, and real window,
run `agent-status`. Only when the user explicitly accepts or rejects that exact
evidence, execute the returned `decision.approval_command` or
an exact `decision.rejection_command`; when rejection is a template, render it
only with `required_inputs.user_feedback`. Then reobserve:

```powershell
.\.venv\Scripts\python.exe .\tools\pet_workspace.py agent-status <pet-id>
```

An accepted build must report `ready_to_release`. Changing only the `sharing`
section does not invalidate runtime review. A changed application source,
packaging resource, runtime config field, recipe, approved source frame, touch
provenance, manifest, or QA does invalidate it and requires a new build and
review.

Release only with:

```powershell
.\scripts\release_pet.ps1 -PetId <pet-id> -Mode share-no-selfie
```

Choose `private-local`, `share-with-approved-selfie`, or `share-no-selfie`.
The release reuses the reviewed runtime, then runs both source and packaged-EXE
double-click chat smoke tests. Deliver the complete-directory ZIP and release
report, never an EXE alone. `package-preparation.json` is schema v2 with a fresh
random `preparation_id`, `prepared_at`, and hashes for QA, the packaged active
pointer, minimal workflow, and optional selfie. Finalize rechecks the current
workflow, QA, reviewed runtime, and share-with selfie authorization; the
packaged selfie must match its recorded hash.

Finalize also requires six schema-v1 passed JSON reports: UnitOnly tests, source
startup, packaged startup, interaction menu, source double-click chat, and
packaged double-click chat. Every non-test report must match the current
`build_id` and `runtime_manifest_sha256`; a hybrid report must also bind the
current pet/runtime. Both packaged reports must bind the final EXE
SHA-256, the entire one-dir `distribution_sha256`, pet ID, and preparation ID;
changing any `_internal` file after those smokes fails finalization. Boolean
flags and exit codes alone are not evidence.

Privacy scanning exempts a `source` directory name only inside bundled
`_internal/PySide6` vendor content. It must still reject private directory names
under `_internal/user_assets`. Smoke tests isolate and restore all overrides,
including `ONEPIC_TESTS_PASSED`, `ONEPIC_INCLUDE_USER_ASSETS`,
`ONEPIC_PACKAGE_USER_ROOT`, and `ONEPIC_INCLUDE_SELFIE_ASSET`.

## Privacy

Never upload private sources. Never package originals, videos, references,
drafts, candidates, backups, or machine paths. A shareable selfie requires an
explicit matching SHA-256 permission. `source/selfie.png` is the optional
original-photo bubble; the animated `actions/transparent/selfie.png` is a
character pose and may be generated from the approved identity anchor.
