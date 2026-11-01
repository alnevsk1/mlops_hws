"""Реальная загрузка Qwen3 baseline/адаптера с условиями оценки ДЗ 6+7."""

from __future__ import annotations

import random


class RealBackend:
    """Загружает одну закреплённую версию и использует её до остановки процесса."""

    def __init__(self, manifest: dict):
        import numpy as np
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        runtime = manifest["runtime"]
        generation = runtime["generation"]
        self.loaded_id = manifest["model_id"]
        self.runtime_sha256 = manifest["runtime_sha256"]
        self.calls = 0
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = getattr(torch, runtime["dtype"])
        seed = generation["seed"]
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        self.tokenizer = AutoTokenizer.from_pretrained(
            runtime["base_model"],
            revision=runtime["base_revision"],
            local_files_only=True,
        )
        model = AutoModelForCausalLM.from_pretrained(
            runtime["base_model"],
            revision=runtime["base_revision"],
            dtype=self.dtype,
            local_files_only=True,
        )
        if runtime.get("adapter"):
            from peft import PeftModel

            model = PeftModel.from_pretrained(model, manifest["artifact_uri"])
        self.model = model.to(self.device).eval()
        self.generation = generation

    def generate(self, prompt: str, limit: int) -> dict:
        import torch

        self.calls += 1
        text = self.tokenizer.apply_chat_template(
            [
                {"role": "system", "content": self.generation["system"]},
                {"role": "user", "content": prompt},
            ],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=self.generation["enable_thinking"],
        )
        batch = self.tokenizer(text, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            output = self.model.generate(
                **batch,
                max_new_tokens=limit,
                do_sample=self.generation["do_sample"],
                pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
            )
        tokens = output[0, batch["input_ids"].shape[1] :]
        return {
            "answer": self.tokenizer.decode(tokens, skip_special_tokens=True),
            "output_tokens": int(tokens.numel()),
        }

    def warmup(self) -> None:
        self.generate("warmup", 1)
