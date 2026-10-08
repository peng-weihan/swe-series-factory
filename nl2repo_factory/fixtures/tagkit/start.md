## TagKit Project Introduction and Goals
TagKit converts comma-separated user tags into an ordered list of normalized, unique tags. Implement the complete Python package from scratch.

## Natural Language Instructions (Prompt)
Create the tagkit package in the current workspace. Implement the public imports and behavior described below. The package must run using the preconfigured environment without fetching code, data, or dependencies.

## Environment Configuration
All implementation paths must be relative to /workspace. Never create, write, or use /testbed.

### Python Version
Python 3.11.

### Core Dependency Library Versions
The project requires only the Python standard library. No additional runtime package installation is required.

## TagKit Project Architecture

### Project Directory Structure
~~~text
workspace/
└── tagkit/
    ├── __init__.py
    ├── api.py
    ├── parser.py
    ├── normalization.py
    └── errors.py
~~~

## API Usage Guide

### Core API

#### 1. Module Import
~~~python
from tagkit import normalize_tags
from tagkit.errors import InvalidTagInput
~~~

#### 2. normalize_tags - Normalize comma-separated tags
**Function**:
Convert a string into an ordered list of normalized, unique nonempty tags.

**Function Signature**:
~~~python
normalize_tags(text: str) -> list[str]
~~~

**Parameter Description**:
text must be a str. Split using the literal comma character. Whitespace around each segment is removed. Normalize each remaining segment using str.casefold.

**Returns**:
A newly produced list[str], preserving the order of first occurrence after normalization. Return [] when there are no nonempty tags. Raise InvalidTagInput before processing when text is not a str.

#### 3. InvalidTagInput - Invalid input type
**Function**:
Signal that normalize_tags received a non-string input.

**Class Definition**:
InvalidTagInput is a public subclass of TypeError, importable from tagkit.errors.

**Parameter Description**:
No additional constructor behavior beyond ordinary TypeError is required.

**Returns**:
This is an exception type. No particular exception message is specified.

## Usage Example
~~~python
from tagkit import normalize_tags

tags = normalize_tags(" Red, BLUE, red ")
print(tags)
# Expected output: ['red', 'blue']
~~~

## Detailed Function Implementation Nodes

### Node 1: Split, normalize, and preserve unique tag order
**Function Description**:
Process comma-separated strings into a stable sequence of tags without mutating external state.

**Handling Strategy**:
Split on commas, strip surrounding whitespace from each segment, apply casefold, and discard empty segments. Deduplicate the resulting strings while preserving their first appearance. Do not sort the result. Each call returns its own list and performs no file or network I/O.

**Input and Output Examples**:
- "Red, Blue" -> ["red", "blue"].
- " red,RED,, blue ,red" -> ["red", "blue"].
- " , , " -> [].
- "Straße,STRASSE" -> ["strasse"].

### Node 2: Validate the input type
**Function Description**:
Reject non-string input consistently through the public exception type.

**Handling Strategy**:
Check the input before performing string processing. Raise InvalidTagInput for a non-str value. Since InvalidTagInput inherits from TypeError, callers catching TypeError must also catch this failure. Do not coerce non-string values to strings.

**Input and Output Examples**:
- None -> raises InvalidTagInput.
- 123 -> raises InvalidTagInput.
- ["red", "blue"] -> raises InvalidTagInput.
