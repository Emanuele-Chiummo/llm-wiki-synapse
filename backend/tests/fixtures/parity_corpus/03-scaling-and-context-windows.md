# Scaling Laws and the Context-Window Debate

As Transformer-based language models grew, two questions came to dominate research: how model
quality scales with size and data, and how far the context window can be pushed. This note records
a genuine tension in the literature.

## Scaling laws

Empirical scaling laws describe how a model's loss falls predictably as parameters, dataset size,
and compute increase together. They were used to justify training ever-larger GPT-style models on
ever-larger corpora, on the premise that capability is a smooth function of scale. Scaling laws are
a claim about the Transformer's behaviour in aggregate, not about the attention mechanism itself.

## The context-window tension

Here the sources disagree, and the disagreement is worth flagging for review.

One line of work argues that the quadratic cost of self-attention makes very long context windows
fundamentally impractical: doubling the sequence length quadruples the attention compute, so beyond
a few thousand tokens the cost is prohibitive and the context window is effectively bounded.

A competing line of work claims the opposite — that the context window is *not* fundamentally
bounded, because sparse, linear, and retrieval-augmented attention variants reduce the cost from
quadratic to near-linear, enabling context windows of hundreds of thousands of tokens in practice.

These two claims about whether the context window is fundamentally limited are in direct conflict.
Which holds depends on assumptions the sources do not fully state, so the question of the practical
ceiling on context length remains open.

## Open question

Is the context window of a Transformer fundamentally bounded by the quadratic cost of self-attention,
or do sub-quadratic attention variants remove that ceiling in practice? The corpus does not resolve
this — it is a genuine open question about the same Transformer architecture and attention mechanism
discussed in the other sources.
