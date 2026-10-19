"""Unit tests for dfx_repair.retrieve.code_context."""

from dfx_repair.retrieve import enclosing_function

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
