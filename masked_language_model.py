import os
import numpy as np
from IPython.display import HTML, display
import torch

import tokenization
from detokenization import html_clean_decoded, html_clean_decoded_logits


def notqdm(it, *a, **k):
    return it


# from https://github.com/huggingface/pytorch-pretrained-BERT/blob/master/run_classifier.py
class InputExample(object):
    """A single training/test example for simple sequence classification."""

    def __init__(self, guid, text_a, text_b=None, label=None):
        """Constructs a InputExample.
        Args:
            guid: Unique id for the example.
            text_a: string. The untokenized text of the first sequence. For single
            sequence tasks, only this sequence must be specified.
            text_b: (Optional) string. The untokenized text of the second sequence.
            Only must be specified for sequence pair tasks.
            label: (Optional) string. The label of the example. This should be
            specified for train and dev examples, but not for test examples.
        """
        self.guid = guid
        self.text_a = text_a
        self.text_b = text_b
        self.label = label


class InputFeatures(object):
    """A single set of features of data."""

    def __init__(self, input_ids, input_mask, segment_ids, label_id, label_weights):
        self.input_ids = input_ids  # inputs tokens with 103 for mask
        self.input_mask = input_mask  # 0 for padding, 1 otherwise
        self.segment_ids = segment_ids  # which sentance it's in
        self.label_id = label_id  # labels, true tokens
        self.label_weights = label_weights


class DataProcessor(object):
    """Base class for data converters for sequence classification data sets."""

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def get_train_examples(self, data_dir):
        """Gets a collection of `InputExample`s for the train set."""
        raise NotImplementedError()

    def get_dev_examples(self, data_dir):
        """Gets a collection of `InputExample`s for the dev set."""
        raise NotImplementedError()

    def get_labels(self):
        """Gets the list of labels for this data set."""
        raise NotImplementedError()


class LMProcessor(DataProcessor):
    """Processor for language modelling."""

    def get_train_examples(self, data_dir, skip=1):
        """See base class."""
        return self._create_examples(
            open(os.path.join(data_dir, "train.txt")).read(), "train", skip=skip
        )

    def get_dev_examples(self, data_dir, skip=1):
        """See base class."""
        return self._create_examples(
            open(os.path.join(data_dir, "val.txt")).read(), "dev", skip=skip
        )

    def get_labels(self):
        """See base class."""
        return list(self.tokenizer.vocab.keys())

    def _create_examples(self, lines, set_type, window_size=300, tqdm=notqdm, skip=1):
        """Creates examples for the training and dev sets."""
        tokens = []
        for line in tqdm(lines.split("\n\n"), desc="tokenising"):
            line = tokenization.convert_to_unicode(line)
            token = self.tokenizer.tokenize(line)
            tokens += token

        examples = []
        for i, start_idx in tqdm(
            list(enumerate(range(0, len(tokens) - window_size - 1, skip))), desc="chunking"
        ):
            guid = "%s-%s" % (set_type, i)
            text_a = tokens[start_idx : start_idx + window_size]
            label = tokens[start_idx + window_size]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=None, label=label)
            )

        if len(examples) == 0:
            guid = "%s-%s" % (set_type, 0)
            text_a = tokens[:-1]
            label = tokens[-1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=None, label=label)
            )

        return examples


def convert_tokens_to_features(
    examples, label_list, max_seq_length, tokenizer, tqdm=notqdm
):
    """Loads a data file into a list of `InputBatch`s."""

    label_map = {}
    for (i, label) in enumerate(label_list):
        label_map[label] = i

    features = []
    for (ex_index, example) in tqdm(list(enumerate(examples))):
        tokens_a = example.text_a

        tokens_b = None
        if example.text_b:
            tokens_b = example.text_b

        if tokens_b:
            # Modifies `tokens_a` and `tokens_b` in place so that the total
            # length is less than the specified length.
            # Account for [CLS], [SEP], [SEP] with "- 3"
            _truncate_seq_pair(tokens_a, tokens_b, max_seq_length - 3)
        else:
            # Account for [CLS] and [SEP] with "- 2"
            if len(tokens_a) > max_seq_length - 2:
                tokens_a = tokens_a[0 : (max_seq_length - 2)]

        # The convention in BERT is:
        # (a) For sequence pairs:
        #  tokens:   [CLS] is this jack ##son ##ville ? [SEP] no it is not . [SEP]
        #  type_ids: 0   0  0    0    0     0       0 0    1  1  1  1   1 1
        # (b) For single sequences:
        #  tokens:   [CLS] the dog is hairy . [SEP]
        #  type_ids: 0   0   0   0  0     0 0
        #
        # Where "type_ids" are used to indicate whether this is the first
        # sequence or the second sequence. The embedding vectors for `type=0` and
        # `type=1` were learned during pre-training and are added to the wordpiece
        # embedding vector (and position vector). This is not *strictly* necessary
        # since the [SEP] token unambigiously separates the sequences, but it makes
        # it easier for the model to learn the concept of sequences.
        #
        # For classification tasks, the first vector (corresponding to [CLS]) is
        # used as as the "sentence vector". Note that this only makes sense because
        # the entire model is fine-tuned.
        tokens = []
        segment_ids = []
        tokens.append("[CLS]")
        segment_ids.append(0)
        for token in tokens_a:
            tokens.append(token)
            segment_ids.append(0)
        tokens.append("[SEP]")
        segment_ids.append(0)

        if tokens_b:
            for token in tokens_b:
                tokens.append(token)
                segment_ids.append(1)
            tokens.append("[SEP]")
            segment_ids.append(1)

        input_ids = tokenizer.convert_tokens_to_ids(tokens)

        # The mask has 1 for real tokens and 0 for padding tokens. Only real
        # tokens are attended to.
        input_mask = [1] * len(input_ids)

        # Zero-pad up to the sequence length.
        while len(input_ids) < max_seq_length:
            input_ids.append(0)
            input_mask.append(0)
            segment_ids.append(0)

        # see https://github.com/google-research/bert/blob/d8014ef72/create_pretraining_data.py#L363
        label_weights = np.random.rand(len(input_ids)) < 0.10  # 10% change of masking
        label_keep = (
            np.random.rand(len(input_ids)) < 0.10
        ) * label_weights  # 10% chance of keeping
        label_switch = (
            np.random.rand(len(input_ids)) < 0.10
        ) * label_weights  # 10% chance of random word
        label_mask = label_weights * (1 - label_keep) * (1 - label_keep)

        switched_ids = np.random.randint(
            low=0, high=len(tokenizer.vocab) - 1, size=(len(input_ids),)
        )

        input_ids_masked = np.array(input_ids.copy())
        input_ids_masked[label_switch == 1] = switched_ids[label_switch == 1]
        input_ids_masked[label_mask == 1] = 103
        input_ids_masked = input_ids_masked.tolist()

        assert len(input_ids) == max_seq_length
        assert len(input_mask) == max_seq_length
        assert len(segment_ids) == max_seq_length
        assert len(input_ids_masked) == max_seq_length

        #         if ex_index < 3:
        #             logger.info("*** Example ***")
        #             logger.info("guid: %s" % (example.guid))
        #             logger.info("tokens: %s" % " ".join(
        #                     [tokenization.printable_text(x) for x in tokens]))
        #             logger.info("input_ids: %s" % " ".join([str(x) for x in input_ids]))
        #             logger.info("input_mask: %s" % " ".join([str(x) for x in input_mask]))
        #             logger.info(
        #                     "segment_ids: %s" % " ".join([str(x) for x in segment_ids]))
        #             logger.info("label: %s (id = %d)\n" % (example.label, label_id))

        features.append(
            InputFeatures(
                input_ids=input_ids_masked,
                input_mask=input_mask,
                segment_ids=segment_ids,
                label_id=input_ids,
                label_weights=label_weights,
            )
        )
    return features


def _truncate_seq_pair(tokens_a, tokens_b, max_length):
    """Truncates a sequence pair in place to the maximum length."""

    # This is a simple heuristic which will always truncate the longer sequence
    # one token at a time. This makes more sense than truncating an equal percent
    # of tokens from each, since if one sequence is very short then each token
    # that's truncated likely contains more information than a longer sequence.
    while True:
        total_length = len(tokens_a) + len(tokens_b)
        if total_length <= max_length:
            break
        if len(tokens_a) > len(tokens_b):
            tokens_a.pop()
        else:
            tokens_b.pop()


def predict_masked_words(
    x, processor, tokenizer, model, n=10, max_seq_length=300, device="cuda"
):
    ex = processor._create_examples(x, "train", tqdm=notqdm)[-1:]
    label_list = processor.get_labels()

    log_feats = convert_tokens_to_features(
        ex, label_list, max_seq_length, tokenizer, tqdm=notqdm
    )

    log_input_ids = torch.tensor([f.input_ids for f in log_feats], dtype=torch.long)
    log_input_mask = torch.tensor([f.input_mask for f in log_feats], dtype=torch.long)
    log_segment_ids = torch.tensor([f.segment_ids for f in log_feats], dtype=torch.long)
    log_label_ids = torch.tensor([f.label_id for f in log_feats], dtype=torch.long)
    log_label_weights = torch.tensor(
        [f.label_weights for f in log_feats], dtype=torch.long
    )

    batch = [
        log_input_ids,
        log_input_mask,
        log_segment_ids,
        log_label_ids,
        log_label_weights,
    ]

    with torch.no_grad():
        batch = tuple(t.to(device) for t in batch)
        input_ids, input_mask, segment_ids, label_ids, label_weights = batch
        logits = model(input_ids, segment_ids, input_mask).detach()

    i = 0
    display(
        HTML(
            html_clean_decoded(
                tokens=log_label_ids[i][1:-2],
                input_mask=input_mask[i][1:-2],
                label_weights=label_weights[i][1:-2],
            ).replace("rgba(255,0,0", "rgba(0,0,255")
        )
    )
    display(
        HTML(
            html_clean_decoded_logits(
                input_ids=input_ids[i][1:-1],
                input_mask=input_mask[i][1:-1],
                logits=logits[i][1:-1],
                label_weights=label_weights[i][1:-1],
            )
        )
    )


# def predictions
def pad_seq(s1, tokenizer, max_seq_length=300):
    # HACK: pad short sentances with
    x = "¿ " * (max_seq_length + 2 - len(tokenizer.tokenize(s1))) + s1
    return x


def predict_next_words(
    text, processor, tokenizer, model, max_seq_length=300, n=10, T=1.0, device="cuda"
):
    """
    Predict next `n` words for some `text`
    Args:
    - text (str) base string, we will predict next words
    - processor
    - tokenizer
    - n (int) amount of words to predict
    - T (float) temperature for when samping predictions
    
    Returns:
    - IPython html object, which show predicted words in red, with opacity indicating confidence
    """
    discarded = []
    x = pad_seq(text, tokenizer=tokenizer, max_seq_length=max_seq_length)
    ex = processor._create_examples(x, "train", tqdm=notqdm)[-1:]
    label_list = processor.get_labels()

    log_feats = convert_tokens_to_features(
        ex, label_list, max_seq_length, tokenizer, tqdm=notqdm
    )

    with torch.no_grad():

        log_input_ids = torch.tensor([f.input_ids for f in log_feats], dtype=torch.long)
        log_input_mask = torch.tensor(
            [f.input_mask for f in log_feats], dtype=torch.long
        )
        log_segment_ids = torch.tensor(
            [f.segment_ids for f in log_feats], dtype=torch.long
        )
        log_label_ids = torch.tensor([f.label_id for f in log_feats], dtype=torch.long)
        log_label_weights = torch.tensor(
            [f.label_weights for f in log_feats], dtype=torch.long
        )

        # Now we only want to predict the next word... so remove our masks
        log_input_ids = log_label_ids * 1
        log_label_weights[:] = 0

        # and add a mask token 2nd to last, and drop the first word (to keep max seq len)
        discarded.append(log_input_ids[0, 1])
        log_input_ids = torch.cat(
            [torch.tensor([[101]]), log_input_ids[:, 2:-1], torch.tensor([[103, 102]])],
            -1,
        )
        log_input_mask = torch.cat(
            [log_input_mask[:, 1:], torch.tensor([[1]])], -1
        )  # Add one to end
        log_label_weights[:, -2] = 1

        batch = [
            log_input_ids,
            log_input_mask,
            log_segment_ids,
            log_label_ids,
            log_label_weights,
        ]
        batch = tuple(t.to(device) for t in batch)
        input_ids, input_mask, segment_ids, label_ids, label_weights = batch

        for i in range(n):
            logits = model(input_ids, segment_ids, input_mask).detach()

            # sample outputs with probability...
            predictions = torch.distributions.Multinomial(logits=logits / T).sample()
            next_word = predictions[:, -2].argmax(dim=-1)

            # Add prediction to end, and update data tensor by rolling the contents
            # drop first part of content, add prediction to end of content (and put sides back: CLS=101 at start, and MASK=103, SEP=102 at end again)
            discarded.append(input_ids[0, 1])
            input_ids = torch.cat(
                [
                    torch.tensor([[101]]).cuda(),
                    input_ids[:, 2:-2],
                    torch.tensor([[next_word, 103, 102]]).cuda(),
                ],
                -1,
            )
            input_mask = torch.cat(
                [input_mask[:, 1:], torch.tensor([[1]]).cuda()], -1
            )  # drop first, add [1] to end
            label_weights = torch.cat(
                [label_weights[:, 1:-1], torch.tensor([[1, 0]]).cuda()], -1
            )  # drop 1st, add 1 to end of content

    #            # I could print probabilities
    #             log_probs = F.log_softmax(logits, -1)
    #             print(decoder[next_word.item()], 'prob={:2.4e}'.format(log_probs[0, -1, next_word.item()].exp().item()))

    input_ids = torch.cat([torch.tensor([discarded]).cuda(), input_ids[:, 2:-2]], -1)
    input_mask = torch.cat(
        [torch.tensor([[1] * len(discarded)]).cuda(), input_mask[:, 2:-2]], -1
    )  # drop first, add [1] to end
    label_weights = torch.cat(
        [torch.tensor([[0] * len(discarded)]).cuda(), label_weights[:, 2:-2]], -1
    )  # drop 1st, add 1 to end of content

    batch = 0
    # return html fragment, cleaned, but cut of the first and last two tokens which are [CLS] and [MASK][SEP]
    return HTML(
        html_clean_decoded(
            tokens=input_ids[batch],
            input_mask=input_mask[batch],
            label_weights=label_weights[batch],
        )
    )
