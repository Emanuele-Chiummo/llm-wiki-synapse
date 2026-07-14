# The Transformer Architecture

The Transformer is a neural network architecture introduced by Vaswani et al. in the 2017 paper
"Attention Is All You Need". It dispenses with recurrence and convolutions entirely, relying
solely on a mechanism called self-attention to model relationships between tokens in a sequence.

## Self-attention

The core idea of the Transformer is the attention mechanism. For each token, self-attention
computes a weighted sum of the representations of all other tokens, where the weights are derived
from the compatibility (dot product) between a query vector and a set of key vectors. This lets the
model relate distant tokens directly, in a single step, rather than propagating information through
many recurrent time steps.

Multi-head attention runs several attention operations in parallel, each with its own learned
projection, so the model can attend to information from different representation subspaces at once.

## Architecture

A Transformer is a stack of identical encoder and decoder layers. Each encoder layer contains a
multi-head self-attention sublayer followed by a position-wise feed-forward network, with residual
connections and layer normalization around each. Because self-attention is permutation-invariant,
the Transformer adds positional encodings to the input embeddings so the model can use token order.

## Impact

The Transformer became the foundation for nearly all modern large language models. Its ability to
be trained efficiently on parallel hardware — a direct consequence of removing sequential
recurrence — is what made training on internet-scale corpora practical. Vaswani's design is now the
default building block for both encoder-only and decoder-only language models.

An important practical property is the context window: the maximum number of tokens the model can
attend over at once. In the original Transformer this was a few hundred tokens, bounded by the
quadratic cost of self-attention in sequence length.
