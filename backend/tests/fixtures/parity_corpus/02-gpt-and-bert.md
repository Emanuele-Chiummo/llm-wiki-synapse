# GPT and BERT: Two Uses of the Transformer

GPT and BERT are two influential language models, both built on the Transformer architecture
introduced by Vaswani et al., but they use it in opposite ways. Comparing them is a clean way to
understand the encoder/decoder split.

## BERT

BERT (Bidirectional Encoder Representations from Transformers), from Google, uses only the encoder
stack of the Transformer. It is trained with a masked-language-modelling objective: random tokens
are hidden and the model predicts them from context on both sides. Because every token attends to
every other token, BERT builds deeply bidirectional representations, which makes it strong for
understanding tasks such as classification and extractive question answering. BERT is not designed
to generate text left to right.

## GPT

GPT (Generative Pre-trained Transformer), from OpenAI, uses only the decoder stack. It is trained
with an autoregressive objective: predict the next token given all previous tokens, using masked
self-attention so a position can only attend to earlier positions. This left-to-right constraint is
exactly what makes GPT a natural text generator.

## Comparison

The essential contrast: BERT reads the whole sequence at once and is optimised for understanding;
GPT reads left to right and is optimised for generation. BERT's bidirectional attention and GPT's
causal attention are both instances of the same self-attention mechanism from the Transformer — the
difference is only in the masking. Later GPT models greatly enlarged the context window relative to
the original Transformer, making long-document generation practical.

Both models depend on the attention mechanism and on the scalability of the Transformer; neither
would exist without Vaswani's architecture.
