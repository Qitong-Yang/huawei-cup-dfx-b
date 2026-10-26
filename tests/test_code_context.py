"""Unit tests for dfx_repair.retrieve.code_context."""

from dfx_repair.retrieve import enclosing_function, statement_span

SOURCE = """export class VM {
  private field: number = 0;

  public customSortResource(index1: number, index2: number) {
    const list = this.list;
    if (index1 >= list.length) {
      return;
    }
    let a = list[index2].sortNum;
  }

  public static verifyCacheFile(filename: string,
    area: number = 1,
    uiContext?: object): void {
    const words = new Int32Array(buffer);
  }
}
""".splitlines()


def test_method_span_not_confused_by_inner_if():
    for line in (6, 9):
        span = enclosing_function(SOURCE, line)
        assert span is not None
        assert (span.name, span.start_line, span.end_line) == ("customSortResource", 4, 10)


def test_multiline_header():
    span = enclosing_function(SOURCE, 15)
    assert span is not None
    assert (span.name, span.start_line, span.end_line) == ("verifyCacheFile", 12, 16)


def test_field_outside_function_returns_class_or_none():
    span = enclosing_function(SOURCE, 2)
    # class headers are not function headers
    assert span is None


def test_statement_span_call_arguments():
    assert statement_span(["foo(a,", "  b,", "  c);"], 2) == (1, 3)


def test_statement_span_method_chain():
    assert statement_span(["x", "  .then(y)", "  .catch(z);"], 3) == (1, 3)


def test_statement_span_single_line_and_parens_in_strings():
    assert statement_span(["let s = '(';", "use(s);"], 2) == (2, 2)


def test_statement_span_does_not_cross_block_braces():
    lines = ["build() {", "  Column() {", "    Text(this.items[", "      0].name)", "  }", "}"]
    assert statement_span(lines, 4) == (3, 4)
