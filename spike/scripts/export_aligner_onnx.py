#!/usr/bin/env python3
"""Exporta el alineador MMS_FA (wav2vec2 CTC de torchaudio) a ONNX y lo cuantiza
a int8, para poder alinear con onnxruntime SIN torch.

Se corre UNA vez (dev) para producir el artefacto; la app solo consume el .onnx.
Salida: spike/models/mms_fa_int8.onnx  (+ el fp32 intermedio, borrable)
"""
from pathlib import Path

import torch
import torchaudio

OUT = Path(__file__).resolve().parent.parent / "models"
OUT.mkdir(parents=True, exist_ok=True)
FP32 = OUT / "mms_fa_fp32.onnx"
INT8 = OUT / "mms_fa_int8.onnx"


class Wrap(torch.nn.Module):
    """El modelo devuelve (emissions, lengths); en ONNX solo queremos emissions."""

    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, waveform):
        out = self.m(waveform)
        return out[0] if isinstance(out, tuple) else out


def main():
    print("cargando MMS_FA…")
    model = torchaudio.pipelines.MMS_FA.get_model(with_star=False).eval()
    wrap = Wrap(model).eval()

    dummy = torch.zeros(1, 16000 * 3)  # 3 s
    print("exportando a ONNX fp32…")
    torch.onnx.export(
        wrap, (dummy,), str(FP32),
        input_names=["waveform"], output_names=["emissions"],
        dynamic_axes={"waveform": {1: "samples"}, "emissions": {1: "frames"}},
        opset_version=17, do_constant_folding=True,
    )
    print(f"  fp32: {FP32.stat().st_size/1e6:.0f} MB")

    print("cuantizando a int8…")
    from onnxruntime.quantization import quantize_dynamic, QuantType
    quantize_dynamic(str(FP32), str(INT8), weight_type=QuantType.QInt8)
    print(f"  int8: {INT8.stat().st_size/1e6:.0f} MB -> {INT8}")


if __name__ == "__main__":
    main()
