"""
LoRA Fine-tuning for Governor Model.
Fine-tunes a Llama-3 8B variant on CRELLA trade decisions and outcomes.
Uses 4-bit quantization + LoRA for efficient H100 training.
"""

import os
import json
import logging
import torch
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [LORA-TRAIN] %(message)s")
log = logging.getLogger(__name__)

ML_DIR = Path(__file__).resolve().parents[1]
DATASET_PATH = ML_DIR / "finetune" / "governor_training.jsonl"
MODEL_DIR = ML_DIR / "models" / "governor_lora"

BASE_MODEL = os.environ.get("LORA_BASE_MODEL", "NousResearch/Meta-Llama-3-8B-Instruct")

LORA_R = 16
LORA_ALPHA = 32
LORA_DROPOUT = 0.05
LEARNING_RATE = 2e-4
EPOCHS = 3
BATCH_SIZE = 4
GRADIENT_ACCUMULATION = 4
MAX_SEQ_LEN = 512


def load_dataset():
    from datasets import Dataset

    records = []
    with open(DATASET_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            messages = rec["messages"]
            text = ""
            for msg in messages:
                role = msg["role"]
                content = msg["content"]
                if role == "system":
                    text += f"<|system|>\n{content}\n"
                elif role == "user":
                    text += f"<|user|>\n{content}\n"
                elif role == "assistant":
                    text += f"<|assistant|>\n{content}\n"
            records.append({"text": text})

    ds = Dataset.from_list(records)
    log.info(f"Loaded {len(ds)} training examples")
    return ds


def train():
    log.info("=" * 60)
    log.info("LoRA Governor Fine-tuning")
    log.info(f"  Base model:  {BASE_MODEL}")
    log.info(f"  Dataset:     {DATASET_PATH}")
    log.info(f"  LoRA r={LORA_R}, alpha={LORA_ALPHA}")
    log.info(f"  Device:      cuda ({torch.cuda.get_device_name(0)})")
    log.info("=" * 60)

    if not DATASET_PATH.exists():
        log.error("Training dataset not found. Run prepare_dataset first.")
        return

    from transformers import (
        AutoTokenizer,
        AutoModelForCausalLM,
        TrainingArguments,
        Trainer,
        DataCollatorForLanguageModeling,
        BitsAndBytesConfig,
    )
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    log.info("Loading base model with 4-bit quantization...")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        quantization_config=bnb_config,
        device_map={"": 0},
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        bias="none",
        task_type="CAUSAL_LM",
    )

    model = get_peft_model(model, lora_config)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    log.info(f"Trainable params: {trainable:,} / {total:,} ({100*trainable/total:.2f}%)")

    dataset = load_dataset()

    def tokenize(examples):
        return tokenizer(
            examples["text"],
            truncation=True,
            max_length=MAX_SEQ_LEN,
            padding="max_length",
        )

    tokenized = dataset.map(tokenize, batched=True, remove_columns=["text"])

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(ML_DIR / "checkpoints" / "lora_governor"),
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRADIENT_ACCUMULATION,
        learning_rate=LEARNING_RATE,
        lr_scheduler_type="cosine",
        warmup_ratio=0.1,
        bf16=True,
        logging_steps=10,
        save_strategy="epoch",
        save_total_limit=2,
        optim="paged_adamw_8bit",
        report_to="none",
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized,
        data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False),
    )

    log.info("Starting LoRA fine-tuning...")
    trainer.train()

    model.save_pretrained(str(MODEL_DIR))
    tokenizer.save_pretrained(str(MODEL_DIR))

    meta = {
        "base_model": BASE_MODEL,
        "lora_r": LORA_R,
        "lora_alpha": LORA_ALPHA,
        "epochs": EPOCHS,
        "n_examples": len(dataset),
        "trained_at": datetime.utcnow().isoformat(),
        "trainable_params": trainable,
        "total_params": total,
    }
    with open(MODEL_DIR / "training_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    log.info(f"LoRA adapter saved to {MODEL_DIR}")
    log.info("Fine-tuning complete")


if __name__ == "__main__":
    train()
