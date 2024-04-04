import pdfplumber
from dataclasses import dataclass, field
import re
import json
import os


@dataclass(order=True)
class Splicer:
    splicer_type: str = field(compare=False)
    start_idx: int
    seq: str = field(compare=False)

    def __post_init__(self):
        self.seq_len = len(self.seq)

    def __repr__(self):
        if self.splicer_type == "number":
            return f"{self.splicer_type} at index {self.start_idx} | seq = {repr(self.seq)}"
        elif self.splicer_type == "question":
            return f"    {self.splicer_type} at index {self.start_idx} | seq = {repr(self.seq)}"


@dataclass
class Pdf2JsonConverter:
    file_path: str
    option_symbol_separator: str
    option_delimiter: str
    question_symbol_separator: str
    question_delimiter: str
    find_correct_on_bold: bool
    json_schema: list = field(default_factory=list)

    def __post_init__(self):
        self.assert_json_schema()
        self.non_ascii_allowed_chars = ["à", "è", "é", "ì", "ò", "ù", '’', '•', '“', '”', '…', '–', "à".upper(), "è".upper(), "é".upper(), "ì".upper(), "ò".upper(), "ù".upper(), '«', '»']

    def assert_json_schema(self):
        """for a given schema constructor, create the corresponding json schema. Commands are as follows:
            - question_id: the question id
            - question: the question body
            - options: iterable of options
            - single_option: options will be a single element in the json object (Es: "option1": "text of option 1", "option2": "text of option 2", ...)
            - answer_str_repr: the string representation of the correct option
            - answer_id: the id of the correct option (represent the index of the correct option in the options iterable)
        Remind that the commands in the json_schema will be the key in the json object.
        """
        for command in self.json_schema:
            # remember to modify implementation if the json schema changes
            if command not in ["question_id", "question", "options", "single_option", "answer_str_repr", "answer_id"]:
                raise ValueError(f"Invalid command {command} in the json schema")

    def make_splicers(self, text_dump):
        option_delimiter_map = {
            "uppercase_letters": "([A-Z])",
            "lowercase_letters": "([a-z])",
            "ticks": "-"
        }
        question_delimiter_map = {
            "numbers": r"\d+",
        }

        # Create the regular expressions
        option_pattern = create_pattern(self.option_symbol_separator, option_delimiter_map[self.option_delimiter])
        question_delimiter = create_pattern(self.question_symbol_separator,
                                            question_delimiter_map[self.question_delimiter])

        # Find matches and create Splicer objects
        splicer_list = create_splicers(option_pattern, "question", text_dump)
        splicer_list += create_splicers(question_delimiter, "number", text_dump)

        # Sort the Splicer objects
        splicer_list.sort()

        return splicer_list

    def extract_text_from_pdf(self, find_correct_on_bold: bool):
        text_container = []
        with pdfplumber.open(self.file_path) as file:
            for page_num in range(len(file.pages)):
                raw_text = file.pages[page_num]
                if find_correct_on_bold:
                    bold_text = raw_text.filter(lambda obj: obj["object_type"] == "char" and "Bold" in obj["fontname"])
                    pre_bold_text_container = bold_text.extract_text().split(self.option_symbol_separator)[1:]
                text_container.append(raw_text.extract_text())
        if find_correct_on_bold:
            bold_text_container = [pre_correct_option.split("\n")[0].strip()
                                   for pre_correct_option in pre_bold_text_container]
        else:
            bold_text_container = [""]
        return text_container, bold_text_container

    def detect_formula(self, text: str):
        """heuristic to detect if a formula is present in the text"""
        replacer = " %FORMULA% "
        non_ascii = [char for idx, char in enumerate(text) if not (char.isascii() or char in self.non_ascii_allowed_chars)]
        if len(non_ascii) > 0:
            for char in non_ascii:
                text = text.replace(char, replacer)
        return text

    def add_fully_parsed_question(self, to_json_dict_container: list[dict, ...],
                                  question_number: int,
                                  question_body: str,
                                  options: list[str, ...],
                                  bold_container: list[str, ...],
                                  ):
        temp_json_dict = {}
        # if the question body and options have been populated
        answer_str_repr_container = [option for option in options if option in bold_container]
        answer_id_container = [options.index(answer_str_repr) for answer_str_repr in
                               answer_str_repr_container] if answer_str_repr_container else [-1]
        for command in self.json_schema:
            if command == "question_id":
                temp_json_dict[command] = question_number
            elif command == "question":
                question_body = self.detect_formula(question_body)
                temp_json_dict[command] = question_body
            elif command == "options":
                temp_json_dict[command] = options.copy()
            elif command == "single_option":
                for i, option in enumerate(options):
                    temp_json_dict[f"option{i + 1}"] = option if i < len(options) else ""
            elif command == "answer_str_repr":
                temp_json_dict[command] = answer_str_repr_container.copy()
            elif command == "answer_id":
                temp_json_dict[command] = answer_id_container.copy()
        to_json_dict_container.append(temp_json_dict.copy())
        return to_json_dict_container

    def get_json(self):
        text_container, bold_container = self.extract_text_from_pdf(self.find_correct_on_bold)
        text = "\n".join(text_container)

        splicer_list = self.make_splicers(text)
        lof_splicer = len(splicer_list)

        question_number: int = 1
        question_body = ""

        splicer_index_offset: int = 0

        options: list[str, ...] = []

        to_json_dict_container: list[dict] = []
        for splicer_idx, splicer in enumerate(splicer_list):
            print(splicer)
            if splicer.splicer_type == "number":
                if splicer_idx == lof_splicer - 1:
                    # if the current splicer is the last one, skip it
                    continue
                if splicer_list[splicer_idx - 1].splicer_type == "number":
                    # if the previous splicer is a number, merge them together because it had a number list in the question
                    splicer_index_offset += 1

                if question_body and options:
                    print(f"{question_body = }")
                    for idx, option in enumerate(options):
                        print(f"    {idx}: {option = }")

                    to_json_dict_container = self.add_fully_parsed_question(to_json_dict_container, question_number,
                                                                            question_body, options, bold_container)
                    question_number += 1
                    splicer_index_offset = 0
                    options.clear()

                # get question body
                prev_splicer = splicer_list[splicer_idx - splicer_index_offset]
                question_body = text[prev_splicer.start_idx + prev_splicer.seq_len:splicer_list[
                    splicer_idx + 1].start_idx].strip().replace("\n", "")

            # get options
            if splicer.splicer_type == "question":
                if splicer_idx == lof_splicer - 1:
                    # if the current splicer is the last one get it
                    parsed_option = \
                        text[splicer.start_idx + splicer.seq_len:].strip().replace("\n", "")
                    options.append(parsed_option)
                    to_json_dict_container = self.add_fully_parsed_question(to_json_dict_container, question_number,
                                                                            question_body, options, bold_container)
                else:
                    # get the current option
                    parsed_option = text[splicer.start_idx + splicer.seq_len:splicer_list[
                        splicer_idx + 1].start_idx].strip().replace("\n", "")
                options.append(parsed_option)

        return to_json_dict_container

    def dump_json(self, file_path: str):
        to_json_dict_container = self.get_json()
        with open(file_path, "w") as file:
            json.dump(to_json_dict_container, file, indent=2)




def create_pattern(delimiter, matcher):
    """creates a regular expression pattern from the delimiter and matcher strings."""
    if delimiter != "":
        delimiter = r"\$from_user$".replace("$from_user$", delimiter)
    else:
        delimiter = r""
    pattern = r"\n\s*$matcher$$delimiter$".replace("$matcher$", matcher).replace("$delimiter$", delimiter)
    return re.compile(pattern)


def create_splicers(pattern, splicer_type, text_dump):
    matches = re.finditer(pattern, text_dump)
    return [Splicer(splicer_type, match.start(), match.group()) for match in matches]


if __name__ == "__main__":
    # Get a list of all files in the current directory
    files = os.listdir()

    # Filter the list to only include PDF files
    pdf_files = [file for file in files if file.endswith('.pdf')]

    # Iterate over the PDF files
    for pdf_file in pdf_files:
        # Create a Pdf2JsonConverter instance for each PDF file
        converter = Pdf2JsonConverter(pdf_file,
                                      ")", "uppercase_letters",
                                      ".", "numbers",
                                      True,
                                      ["question_id", "question", "options", "single_option", "answer_str_repr",
                                       "answer_id"])
        # Replace the .pdf extension with .json for the output file
        output_file = pdf_file.replace('.pdf', '.json')

        # Convert the PDF to JSON and save it
        converter.dump_json(output_file)