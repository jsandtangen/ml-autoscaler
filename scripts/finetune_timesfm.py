"""Fine-tune a TimesFM LoRA adapter and compare on a chronological holdout."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import logging
import math
import random
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from scaler.forecasting import load_player_history
from scaler.training import chronological_split, score_predictions


MODEL_ID = "google/timesfm-2.5-200m-transformers"
logger = logging.getLogger("ruby_acorn.training")


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-path", type=Path, required=True)
    parser.add_argument("--appid", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--context-length", type=positive_int, default=192)
    parser.add_argument("--horizon", type=positive_int, default=1)
    parser.add_argument("--validation-size", type=positive_int, default=72)
    parser.add_argument("--test-size", type=positive_int, default=72)
    parser.add_argument("--epochs", type=positive_int, default=3)
    parser.add_argument("--batch-size", type=positive_int, default=4)
    parser.add_argument("--learning-rate", type=float, default=5e-5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threads", type=positive_int, default=4)
    parser.add_argument("--model-cache-dir", default=".model-cache")
    parser.add_argument("--local-files-only", action="store_true")
    args = parser.parse_args(argv)
    if args.context_length % 32 or args.context_length > 16384:
        parser.error("Context length must be a multiple of 32, at most 16384")
    if args.horizon > 128:
        parser.error("This training experiment supports horizons up to 128")
    if not math.isfinite(args.learning_rate) or args.learning_rate <= 0:
        parser.error("Learning rate must be finite and positive")
    return args


def train(args) -> dict:
    # Validate data and output before importing ML libraries or loading weights.
    games = load_player_history(args.history_path)
    if args.appid not in games:
        raise ValueError(f"No history for appid {args.appid!r}")
    observations = games[args.appid]
    split = chronological_split(observations, args.context_length, args.horizon,
                                args.validation_size, args.test_size)
    if args.output_dir.exists():
        raise ValueError("Output directory already exists; choose a new run directory")
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import TimesFm2_5ModelForPrediction
    from huggingface_hub import hf_hub_download

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(args.threads)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    args.output_dir.mkdir(parents=True)
    report = {
        "status": "running", "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": MODEL_ID, "appid": args.appid, "device": device,
        "history_path": str(args.history_path),
        "history_sha256": hashlib.sha256(args.history_path.read_bytes()).hexdigest(),
        "parameters": {key: str(value) if isinstance(value, Path) else value
                       for key, value in vars(args).items()},
        "versions": {name: importlib.metadata.version(name) for name in ("torch", "transformers", "peft")},
        "splits": {
            "observations": len(observations), "interval_seconds": split.interval_seconds,
            "train_observations": split.train_end,
            "validation_observations": args.validation_size, "test_observations": args.test_size,
            "train_windows": len(split.train), "validation_windows": len(split.validation),
            "test_windows": len(split.test),
            "validation_start_timestamp": observations[split.train_end].timestamp,
            "test_start_timestamp": observations[split.validation_end].timestamp,
            "test_end_timestamp": observations[-1].timestamp,
            "protocol": "rolling observed context; targets strictly separated chronologically",
        },
        "epochs": [],
    }

    def write_report():
        (args.output_dir / "report.json").write_text(
            json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")

    write_report()
    logger.info("Loading %s on %s; %s training windows", MODEL_ID, device, len(split.train))
    base = TimesFm2_5ModelForPrediction.from_pretrained(
        MODEL_ID, cache_dir=args.model_cache_dir, local_files_only=args.local_files_only,
        dtype=torch.float32,
    ).to(device)
    # Preserve checkpoint forecast settings for all before/after comparisons.
    checkpoint_path = Path(hf_hub_download(
        MODEL_ID, "model.safetensors", cache_dir=args.model_cache_dir, local_files_only=True,
    ))
    report["checkpoint_revision"] = checkpoint_path.parent.name
    report["forecast_configuration"] = {
        name: getattr(base.config, name) for name in
        ("force_flip_invariance", "use_continuous_quantile_head", "infer_is_positive", "decode_index")
    }
    if args.context_length > base.config.context_length:
        raise ValueError("Context exceeds checkpoint context length")

    def tensors(windows):
        return (
            torch.tensor([w.context for w in windows], dtype=torch.float32, device=device),
            torch.tensor([w.targets for w in windows], dtype=torch.float32, device=device),
        )

    def predict(model, windows):
        model.eval()
        values = []
        with torch.no_grad():
            for start in range(0, len(windows), args.batch_size):
                context, _ = tensors(windows[start:start+args.batch_size])
                outputs = model(past_values=context, forecast_context_len=args.context_length)
                values.extend(outputs.mean_predictions[:, args.horizon-1].float().cpu().tolist())
        return values

    validation_targets = [w.targets[-1] for w in split.validation]
    base_validation = score_predictions(predict(base, split.validation), validation_targets)
    report["zero_shot_validation"] = asdict(base_validation)
    model = get_peft_model(base, LoraConfig(
        r=4, lora_alpha=8, target_modules="all-linear", lora_dropout=0.05, bias="none",
    ))
    trainable = [p for p in model.parameters() if p.requires_grad]
    report["trainable_parameters"] = sum(p.numel() for p in trainable)
    report["total_parameters"] = sum(p.numel() for p in model.parameters())
    optimizer = torch.optim.AdamW(trainable, lr=args.learning_rate, weight_decay=0.01)
    adapter_dir = args.output_dir / "adapter"
    best_mae = float("inf")
    for epoch in range(1, args.epochs+1):
        model.train()
        order = torch.randperm(len(split.train)).tolist()
        total_loss, examples = 0.0, 0
        for step, start in enumerate(range(0, len(order), args.batch_size), 1):
            windows = [split.train[i] for i in order[start:start+args.batch_size]]
            context, target = tensors(windows)
            optimizer.zero_grad(set_to_none=True)
            loss = model(past_values=context, future_values=target,
                         forecast_context_len=args.context_length).loss
            if loss is None or not torch.isfinite(loss):
                raise ValueError("Training produced nonfinite or missing loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(trainable, 1.0, error_if_nonfinite=True)
            optimizer.step()
            total_loss += loss.item()*len(windows)
            examples += len(windows)
            if step == 1 or step % 10 == 0:
                logger.info("epoch=%s step=%s loss=%.6f", epoch, step, loss.item())
        validation = score_predictions(predict(model, split.validation), validation_targets)
        report["epochs"].append({"epoch": epoch, "training_loss": total_loss/examples,
                                 "validation": asdict(validation)})
        if validation.mae < best_mae:
            best_mae = validation.mae
            report["best_epoch"] = epoch
            model.save_pretrained(adapter_dir)
        write_report()
        logger.info("epoch=%s validation_mae=%.2f best_epoch=%s", epoch, validation.mae, report["best_epoch"])

    # Discard final-epoch weights; restore the adapter chosen using validation only.
    base = model.unload()
    model = PeftModel.from_pretrained(base, adapter_dir).to(device)
    model.eval()
    targets = [w.targets[-1] for w in split.test]
    logger.info("Evaluating selected adapter and zero-shot model on held-out test targets")
    tuned = predict(model, split.test)
    with model.disable_adapter():
        zero_shot = predict(model, split.test)
    forecasts = {
        "persistence": [float(w.context[-1]) for w in split.test],
        "moving_average": [sum(w.context)/len(w.context) for w in split.test],
        "timesfm_zero_shot": zero_shot, "timesfm_lora": tuned,
    }
    report["test_scores"] = {name: asdict(score_predictions(values, targets))
                             for name, values in forecasts.items()}
    report["adapter_beats_base_on_validation"] = best_mae < base_validation.mae
    report["adapter_weights_changed"] = any(
        bool(torch.count_nonzero(value)) for name, value in model.state_dict().items() if "lora_B" in name)
    report["status"] = "complete"
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["limitations"] = [
        "Small, previously inspected third-party dataset; exploratory experiment only.",
        "Pretraining overlap is unknown; this is a holdout from our fine-tuning only.",
        "Hourly snapshots cannot validate minute-scale scaling or real server capacity.",
    ]
    with (args.output_dir / "test_predictions.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["timestamp", "actual", *forecasts])
        for i, window in enumerate(split.test):
            writer.writerow([observations[window.target_indices[-1]].timestamp, targets[i],
                             *(values[i] for values in forecasts.values())])
    write_report()
    return report


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    output_existed = args.output_dir.exists()
    try:
        report = train(args)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        logger.error("Fine-tuning failed: %s", error)
        report_path = args.output_dir / "report.json"
        if not output_existed and report_path.exists():
            failed_report = json.loads(report_path.read_text(encoding="utf-8"))
            failed_report.update(status="failed", error=str(error))
            report_path.write_text(json.dumps(failed_report, indent=2)+"\n", encoding="utf-8")
        return 1
    print(json.dumps(report["test_scores"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
