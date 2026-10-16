"""Checks actual Torch/Transformers tensor APIs with a tiny random CLIP.
No downloaded weights; this does NOT measure classification quality.
"""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('transformers'), 'AI dependencies not installed')
class ClipRuntimeTests(unittest.TestCase):
    def test_clip_tensor_pipeline(self):
        import torch
        from transformers import CLIPConfig, CLIPImageProcessor, CLIPModel
        from PIL import Image
        torch.manual_seed(0)
        model = CLIPModel(CLIPConfig(
            projection_dim=16,
            text_config={'vocab_size': 32, 'hidden_size': 16, 'intermediate_size': 32, 'num_hidden_layers': 1, 'num_attention_heads': 2, 'max_position_embeddings': 16, 'eos_token_id': 2, 'bos_token_id': 1},
            vision_config={'hidden_size': 16, 'intermediate_size': 32, 'num_hidden_layers': 1, 'num_attention_heads': 2, 'image_size': 16, 'patch_size': 8},
        ))
        class Processor:
            def __call__(self, text=None, images=None, **kwargs):
                if text is not None:
                    return {'input_ids': torch.tensor([[1, 3 + i, 2] for i in range(len(text))])}
                return CLIPImageProcessor(size={'shortest_edge': 16}, crop_size={'height': 16, 'width': 16})(images=images, return_tensors='pt')
        with patch('transformers.CLIPProcessor.from_pretrained', return_value=Processor()), patch('transformers.CLIPModel.from_pretrained', return_value=model):
            classifier = worker.ClipClassifier()
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'test.png'
                Image.new('RGB', (24, 16), 'green').save(path)
                label, score = classifier.predict(path)
                self.assertIn(label, worker.CATEGORIES)
                self.assertGreater(score, 0)
                self.assertLessEqual(score, 1)
