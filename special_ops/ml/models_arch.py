"""Shared model architectures for training and serving."""

import torch
import torch.nn as nn

SEQUENCE_LEN = 32
NUM_FEATURES = 13
HIDDEN_DIM = 128
ATTENTION_HEADS = 4
DROPOUT = 0.2


class Attention(nn.Module):
    def __init__(self, hidden_dim):
        super().__init__()
        self.attn = nn.MultiheadAttention(hidden_dim, ATTENTION_HEADS, batch_first=True, dropout=DROPOUT)
        self.norm = nn.LayerNorm(hidden_dim)

    def forward(self, x):
        attn_out, _ = self.attn(x, x, x)
        return self.norm(x + attn_out)


class DirectionPredictor(nn.Module):
    def __init__(self, input_dim=NUM_FEATURES, hidden_dim=HIDDEN_DIM):
        super().__init__()
        self.lstm = nn.LSTM(
            input_dim, hidden_dim,
            num_layers=2,
            batch_first=True,
            dropout=DROPOUT,
            bidirectional=True,
        )
        self.attention = Attention(hidden_dim * 2)
        self.classifier = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.GELU(),
            nn.Dropout(DROPOUT),
            nn.Linear(hidden_dim, 64),
            nn.GELU(),
            nn.Dropout(DROPOUT),
            nn.Linear(64, 2),
        )

    def forward(self, x):
        lstm_out, _ = self.lstm(x)
        attn_out = self.attention(lstm_out)
        last = attn_out[:, -1, :]
        return self.classifier(last)
