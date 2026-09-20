# Appointment decision dataset format

The local appointment model is trained and evaluated from UTF-8 JSONL. This
repository contains only validators and evaluation code. Raw records,
generated corpora, adapters, checkpoints, and GGUF files must live outside the
application Git repository.

Each line is one independent object:

```json
{
  "sample_id": "apt-000001",
  "cluster_id": "template-relative-time-repair-01",
  "split": "train",
  "messages": [{"role": "user", "content": "周六想约洗衣机维修"}],
  "decision": {
    "action": "ask_user",
    "slots": {"product_ref": "洗衣机"},
    "missing_slots": ["region", "date_range"]
  },
  "source_type": "anonymous_template",
  "license_ref": "internal-template-policy-v1"
}
```

`source_type` is one of `licensed_public`, `anonymous_template`, or
`human_authored`. Public data must retain a resolvable license or dataset-card
identifier in `license_ref`.

Before training, run:

```powershell
python -m scripts.validate_appointment_dataset --input D:\data\appointment.jsonl
```

Use `--formal` for a frozen evaluation release. Formal validation requires at
least 150 evaluation samples. The validator rejects duplicate sample IDs,
train/eval cluster leakage, invalid decision contracts, phone numbers, email
addresses, and explicit detailed-address fields. Its manifest contains only
aggregate counts and a stable SHA-256 digest; it does not copy sample text.
