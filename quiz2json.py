import pdfplumber
from dataclasses import dataclass, field
import re


@dataclass(order=True)
class Splicer:
    splicer_type: str = field(compare=False)
    start_idx: int
    seq: str = field(compare=False)

    def __post_init__(self):
        self.seq_len = len(self.seq)

    def __repr__(self):
        if self.splicer_type == "question":
            return f"{self.splicer_type}: {repr(self.seq)} at index {self.start_idx}"
        elif self.splicer_type == "number":
            return f"    {self.splicer_type}: {repr(self.seq)} at index {self.start_idx}"


@dataclass
class Pdf2JsonConverter:
    file_path: str
    option_symbol_separator: str
    option_delimiter: str
    question_symbol_separator: str
    question_delimiter: str
    json_schema: list = field(default_factory=list)

    def __post_init__(self):
        self.assert_json_schema()

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

    def extract_text_from_pdf(self):
        text_container = []
        with pdfplumber.open(self.file_path) as file:
            for page_num in range(len(file.pages)):
                raw_text = file.pages[page_num]
                bold_text = raw_text.filter(lambda obj: obj["object_type"] == "char" and "Bold" in obj["fontname"])
                pre_bold_text_container = bold_text.extract_text().split(self.option_symbol_separator)[1:]
                text_container.append(raw_text.extract_text())
        bold_text_container = [pre_correct_option.split("\n")[0].strip() for pre_correct_option in pre_bold_text_container]
        return text_container, bold_text_container

    def get_json(self):
        text_container, bold_container = self.extract_text_from_pdf()
        text = "\n".join(text_container)

        splicer_list = self.make_splicers(text)
        lof_splicer = len(splicer_list)
        question_number: int = 1
        question_body = ""
        options: list[str, ...] = []

        to_json_dict_container: list[dict] = []
        temp_json_dict = {}
        for splicer_idx, splicer in enumerate(splicer_list):
            if splicer.splicer_type == "number":
                if splicer_idx == lof_splicer - 1 or splicer_list[splicer_idx + 1].splicer_type != "question":
                    # if the current splicer is the last one or the next splicer is not a question, skip it
                    continue

                if question_body and options:
                    # if the question body and options have been populated
                    answer_str_repr_container = [option for option in options if option in bold_container]
                    answer_id_container = [options.index(answer_str_repr) for answer_str_repr in
                                           answer_str_repr_container] if answer_str_repr_container else [-1]
                    for command in self.json_schema:
                        if command == "question_id":
                            temp_json_dict[command] = question_number
                        elif command == "question":
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
                    question_number += 1
                    options.clear()

                # get question body
                question_body = text[splicer.start_idx + splicer.seq_len:splicer_list[
                    splicer_idx + 1].start_idx].strip().replace("\n", "")

            # get options
            if splicer.splicer_type == "question":
                if splicer_idx == lof_splicer - 1:
                    # if the current splicer is the last one get it
                    parsed_option = \
                        text[splicer.start_idx + splicer.seq_len:].strip().replace("\n", "")
                else:
                    # get the current option
                    parsed_option = text[splicer.start_idx + splicer.seq_len:splicer_list[
                        splicer_idx + 1].start_idx].strip().replace("\n", "")
                options.append(parsed_option)

        return to_json_dict_container


def create_pattern(delimiter, matcher):
    """creates a regular expression pattern from the delimiter and matcher strings."""
    if delimiter != "":
        delimiter = r"\$from_user$".replace("$from_user$", delimiter)
    else:
        delimiter = r""
    pattern = r"\n\s*$matcher$$delimiter$".replace("$matcher$", matcher).replace("$delimiter$", delimiter)
    return re.compile(pattern)


def create_splicers(pattern, splicer_type, text_dump):
    matches = re.finditer(pattern, text_dump.lower())
    return [Splicer(splicer_type, match.start(), match.group()) for match in matches]
