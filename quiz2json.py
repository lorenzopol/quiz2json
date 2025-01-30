import pdfplumber
from dataclasses import dataclass, field

import re
import json
import gzip
import time

from typing import *
import os
import sys


@dataclass
class LineType:
    """line_type is either
        'Q': for question
        'O': for option
        'C': for line continuation"""
    line_raw: dict[str, Any]
    line_type: str

    def __post_init__(self):
        self.line_text = self.line_raw["text"]

    def __repr__(self):
        return f"[T:{self.line_type}] -> {self.line_raw['text']}"


@dataclass
class Question:
    question_number: int | None = None
    question_body: str | None = None
    options: list[str, ...] | None = field(default_factory=list)
    answer_idx_container: list[int, ...] | None = field(default_factory=lambda: [-1])

    answer_str_container: list[str, ...] | None = field(default_factory=list)
    single_options: dict[str, str] | None = field(default_factory=dict)

    def __repr__(self):
        option_str_repr = '\n    '.join(self.options)
        return f"{'=' * 46}\n" \
               f"Question {self.question_number}: {self.question_body}\n" \
               f"{'    ' + option_str_repr}\n" \
               f"correct: {self.answer_idx_container}\n"

    def is_ready(self, drop_questions_without_correct_answer, drop_questions_without_options) -> bool:
        base_required_fields = [self.question_number, self.question_body]
        if drop_questions_without_correct_answer:
            if -1 in self.answer_idx_container:
                self.answer_idx_container.remove(-1)
            base_required_fields += [self.answer_idx_container]
        if drop_questions_without_options:
            base_required_fields += [self.options]
        return all(base_required_fields)

    def populate_last_fields(self):
        if len(self.options) == 0:
            print(f"[WARNING]: No options found for question {self.question_number}")
            return
        self.single_options = {f"option{i + 1}": option for i, option in enumerate(self.options)}
        if len(self.answer_idx_container) > 0:
            self.answer_str_container = [self.options[answer_idx] for answer_idx in self.answer_idx_container if
                                         answer_idx != -1]

    def to_json(self, minimize_size: bool):
        if minimize_size:
            return {
                "question_number": self.question_number,
                "question_body": self.question_body,
                "options": self.options,
                "answer_idx_container": self.answer_idx_container
            }
        else:
            return self.__dict__


@dataclass
class Configs:
    find_correct_answer: bool
    correct_answer_identifier: str
    drop_questions_without_correct_answer: bool
    drop_questions_without_options: bool

    question_identifier: str
    question_symbol_separator: str
    option_identifier: str
    option_symbol_separator: str

    minimize_json_size: bool

    def __post_init__(self):
        # todo: maybe "italic", "underline" work as bold
        self._allowed_correct_answer_identifiers = ["bold", "highlight"]
        self._allowed_question_identifiers = {
            "numbers": "d+",
        }
        self._allowed_question_symbol_separators = [".", ")"]
        self._allowed_option_identifiers = {
            "lowercase_letters": "[a-z]",
            "uppercase_letters": "[A-Z]",
            "ticks": "-",
            "numbers": "d+"
        }
        self._allowed_option_symbol_separators = [".", ")", ""]

        self.question_matcher = re.compile(
            rf"\ {self._allowed_question_identifiers[self.question_identifier]}\ {self.question_symbol_separator}\s".replace(
                " ", ""))
        self.option_matcher = re.compile(
            rf"{self._allowed_option_identifiers[self.option_identifier]}{self.option_symbol_separator}\s")

    def validate_config(self) -> tuple[int, str]:
        """Returns:
            > 1: if the config is valid
            > 0: if there is a warning
            > -1: if there is an error
            """
        if self.find_correct_answer and not self.correct_answer_identifier:
            return -1, f"ERROR! Inconsistent parameters. Search correct answers is set to {self.find_correct_answer} but Correct answer identifier is not given. No matching can be done without a characteristic to find the correct answer."
        if self.drop_questions_without_correct_answer and not self.find_correct_answer:
            return -1, f"ERROR! Inconsistent parameters. Discard if no correct answers is set to {self.drop_questions_without_correct_answer} but Search correct answers is set to {self.find_correct_answer}. We can't drop questions without correct answer if we don't look for correct answers"
        if self._allowed_question_identifiers.get(self.question_identifier) is None:
            return -1, f"ERROR! {self.question_identifier} is not a valid question identifier. Use one of {list(self._allowed_question_identifiers.keys())}"
        if self.question_symbol_separator not in self._allowed_question_symbol_separators:
            return -1, f"ERROR! {self.question_symbol_separator} is not a valid question symbol separator. Use one of {self._allowed_question_symbol_separators}"
        if self._allowed_option_identifiers.get(self.option_identifier) is None:
            return -1, f"ERROR! {self.option_identifier } is not a valid option identifier. Use one of {list(self._allowed_option_identifiers.keys())}"
        if self.option_symbol_separator not in self._allowed_option_symbol_separators:
            return -1, f"ERROR! {self.option_symbol_separator } is not a valid option symbol separator. Use one of {self._allowed_option_symbol_separators}"
        if not self.find_correct_answer and self.correct_answer_identifier:
            return 0, f"WARNING! Inconsistent parameters. Search correct answers is set to {self.find_correct_answer} but Correct answer identifier is given"
        return 1, ""

    def save_to_txt(self, username):
        config_path = os.path.join(os.path.dirname(__file__), "q2j_config", f"config_{username}.txt")
        with open(config_path, "w+") as file:
            for key, value in self.__dict__.items():
                if key.startswith("_") or key == "question_matcher" or key == "option_matcher":
                    continue
                file.write(f"{key}={value}\n")

        return config_path


def load_config(config_path: str):
    with open(config_path, "r") as file:
        config_dict = {}
        for line in file.readlines():
            key, value = line.strip().split("=")
            config_dict[key] = value
    return Configs(**config_dict)


def dump_json(json_container, output_json_path: str):
    with open(output_json_path, "w") as file:
        json.dump(json_container, file, indent=2)


def get_answer_on_bold(chars, config: Configs):
    # on average the first bold char is the one at index 1. Maybe we should be a little more flexible
    if len(chars) > 1:
        return config.correct_answer_identifier in chars[1]["fontname"].lower()


def get_answer_on_highlight(chars, pages):
    # refer to https://en.wikipedia.org/wiki/PNG
    if len(chars) >= 3:
        char = chars[2]
        page = pages[char["page_number"] - 1]
        char_bbox = (char['x0'], page.height - char['y1'], char['x1'], page.height - char['y0'])
        image = page.crop(char_bbox).to_image(resolution=72)
        image_bytes: bytes = image._repr_png_()
        PLTE_index = image_bytes.find(b"PLTE")
        # IHDR_index = image_bytes.find(b"IHDR")
        # IDAT_index = image_bytes.find(b"IDAT")
        # IEND_index = image_bytes.find(b"IEND")
        color_palette = image_bytes[PLTE_index + 4:PLTE_index + 4 + 3]
        return color_palette != b"\xff\xff\xff"


def regex_startswith(line, pattern):
    if re.match(pattern, line) is None:
        return False
    return re.match(pattern, line).start() == 0


def detect_correct_answer(question: Question, line: LineType, config: Configs, pages):
    if config.correct_answer_identifier == "bold":
        if get_answer_on_bold(line.line_raw["chars"], config):
            if -1 in question.answer_idx_container:
                question.answer_idx_container.pop()
            question.answer_idx_container.append(len(question.options) - 1)
    elif config.correct_answer_identifier == "highlight":
        if get_answer_on_highlight(line.line_raw["chars"], pages):
            if -1 in question.answer_idx_container:
                question.answer_idx_container.pop()
            question.answer_idx_container.append(len(question.options) - 1)


def convert(path_to_source_pdf, config: Configs):
    # parse pdf
    print(f"{'=' * 20} BEGIN PARSING {'=' * 20}")
    with pdfplumber.open(path_to_source_pdf) as pdf_file:
        line_type_stack: list[LineType, ...] = []
        trash_x_threshold = None
        for page_num in range(len(pdf_file.pages)):
            # print(f"{'=' * 20} {page_num} {'=' * 20}")
            page = pdf_file.pages[page_num]
            # print(page.extract_text())
            for line_number, line in enumerate(page.extract_text_lines()):
                line_text = line['text']
                if regex_startswith(line_text, config.question_matcher):
                    line_type_stack.append(LineType(line, 'Q'))
                    if trash_x_threshold is None:
                        trash_x_threshold = line['x0']

                # we have an option
                elif regex_startswith(line_text, config.option_matcher):
                    line_type_stack.append(LineType(line, 'O'))

                # we have a continuation or trash
                else:
                    if trash_x_threshold is not None and line['x0'] > trash_x_threshold:
                        # allow as option also those not trash line that starts with a symbol. Space is not
                        # considered because extract_lines returns stripped text
                        if not line_text[0].isalnum():
                            line_type_stack.append(LineType(line, 'O'))
                        else:
                            line_type_stack.append(LineType(line, 'C'))
                    # if we have trash, we don't append it to the stack

            # print(f"{'=' * 20}   {'=' * 20}")
    print(f"{'*' * 20} PARSING OVER {'*' * 20}")
    print(f"{'=' * 20} BEGIN QUESTION PACKING {'=' * 20}")
    # add fake end question for processing the last question of the page
    line_type_stack.append(LineType({"text": f"-1{config.question_symbol_separator}"}, "Q"))
    questions_stack: list[Question, ...] = []
    question = Question()
    pages = pdf_file.pages if config.correct_answer_identifier == "highlight" else None

    for idx, line in enumerate(line_type_stack):
        # print(line)
        if line.line_type == "Q":
            if question.is_ready(config.drop_questions_without_correct_answer,
                                 config.drop_questions_without_options):
                question.populate_last_fields()
                question.question_number = len(questions_stack) + 1
                questions_stack.append(question)
            question = Question()
            split_idx = line.line_text.index(config.question_symbol_separator)
            question_number, question_body = line.line_text[:split_idx], line.line_text[split_idx + 1:]
            question.question_body = question_body.strip()
            question.question_number = int(question_number.strip())

        elif line.line_type == "O":
            # drop the first char because it's the option symbol. Split at space to drop everything before the first space
            option = ' '.join(line.line_text[1:].split(" ")[1:])
            question.options.append(option)
            if config.find_correct_answer:
                detect_correct_answer(question, line, config, pages)
        elif line.line_type == "C":
            if line_type_stack[idx - 1].line_type == "O":
                question.options[-1] += " " + line.line_text
            elif line_type_stack[idx - 1].line_type == "Q":
                question.question_body += " " + line.line_text
    print(f"{'*' * 20} QUESTION PACKING OVER {'*' * 20}")
    json_container = [question.to_json(config.minimize_json_size) for question in questions_stack]
    return json_container


def compress_json(json_container):
    json_data = json.dumps(json_container, indent=2)
    encoded_json = json_data.encode("utf-8")
    return gzip.compress(encoded_json)


def decompress_json(path_to_json):
    with gzip.open(path_to_json, "rb") as file:
        data = file.read()
    return json.loads(data)


def delete_after_delta_time(watch_directory, delta_days):
    delta_time = delta_days * 24 * 60 * 60
    for directory in os.listdir(watch_directory):
        print(f"Directory: {directory}")
        full_dir_path = os.path.join(watch_directory, directory)
        if not os.path.isdir(full_dir_path):
            continue
        for file in os.listdir(full_dir_path):
            file_path = os.path.join(watch_directory, directory, file)
            if os.path.isfile(file_path):
                if time.time() - os.path.getctime(file_path) > delta_time:
                    print(f"Deleting {file_path}")


def main():
    path_to_pdf = r"crocette.pdf"
    config = Configs(find_correct_answer=True, correct_answer_identifier="highlight",
                     drop_questions_without_correct_answer=False, drop_questions_without_options=True,
                     question_identifier="numbers", question_symbol_separator=".",
                     option_identifier="ticks", option_symbol_separator="",
                     minimize_json_size=True)

    json_container = convert(path_to_pdf, config)
    dump_json(json_container, os.path.join(os.getcwd(), path_to_pdf.replace(".pdf", ".json")))


if __name__ == "__main__":
    main()
