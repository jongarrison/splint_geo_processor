"""
DataTreeUtils.py
Helpers for rearranging Grasshopper DataTree contents.

Core operations work on a plain "grid" (list-of-lists, row-major):
    grid[r][c]  -- row r, column c
where each inner list is one branch of the tree (a "row"), and the
i-th item of every branch forms a "column".

Small helpers convert to/from Grasshopper.DataTree so a GH Python 3
component can do:

    from DataTreeUtils import tree_to_grid, grid_to_tree, reverse_columns
    grid = tree_to_grid(input_tree)
    flipped = reverse_columns(grid, [1, 3])
    a = grid_to_tree(flipped)
"""


def reverse_columns(grid, column_indices):
    """Return a new grid with the row-order of the given columns reversed.

    This is a vertical flip applied only to the selected columns; all other
    columns and the overall shape are preserved.

    Args:
        grid: list of lists (rows). All rows must be the same length.
        column_indices: iterable of int column indices to flip.

    Returns:
        A new list-of-lists with the same shape as the input.

    Raises:
        ValueError: if the grid is empty, ragged, or an index is out of range.
    """
    if not grid:
        raise ValueError("grid is empty")

    row_count = len(grid)
    col_count = len(grid[0])
    for r, row in enumerate(grid):
        if len(row) != col_count:
            raise ValueError(
                "grid is ragged: row 0 has {} items, row {} has {}".format(
                    col_count, r, len(row)))

    cols = set()
    for c in column_indices:
        if c < 0 or c >= col_count:
            raise ValueError(
                "column index {} out of range [0, {})".format(c, col_count))
        cols.add(c)

    # Copy rows so we don't mutate the input.
    out = [list(row) for row in grid]

    # For each selected column, take the values top-to-bottom and write
    # them back bottom-to-top.
    for c in cols:
        column_values = [out[r][c] for r in range(row_count)]
        column_values.reverse()
        for r in range(row_count):
            out[r][c] = column_values[r]

    return out


def reorder_column_rows(grid, row_order, column_indices=None):
    """Apply a row-index permutation to selected columns of the grid.

    "Pull" semantics: for each affected column c,
        new_grid[i][c] = grid[row_order[i]][c]
    Rows of unaffected columns are left in place.

    Example: row_order=[0, 4, 1, 3, 2] on a 5-row grid means the new
    column is built by taking row 0, then row 4, then row 1, then row 3,
    then row 2.

    Args:
        grid: list of lists (rows). All rows must be the same length.
        row_order: a permutation of range(len(grid)) -- every row index
                   must appear exactly once.
        column_indices: iterable of int column indices to reorder, or
                        None (default) to apply to all columns.

    Returns:
        A new list-of-lists with the same shape as the input.

    Raises:
        ValueError: if the grid is empty/ragged, row_order is not a valid
                    permutation, or a column index is out of range.
    """
    if not grid:
        raise ValueError("grid is empty")

    row_count = len(grid)
    col_count = len(grid[0])
    for r, row in enumerate(grid):
        if len(row) != col_count:
            raise ValueError(
                "grid is ragged: row 0 has {} items, row {} has {}".format(
                    col_count, r, len(row)))

    row_order = list(row_order)
    if len(row_order) != row_count:
        raise ValueError(
            "row_order has {} entries but grid has {} rows".format(
                len(row_order), row_count))
    if sorted(row_order) != list(range(row_count)):
        raise ValueError(
            "row_order must be a permutation of 0..{} (got {})".format(
                row_count - 1, row_order))

    if column_indices is None:
        cols = set(range(col_count))
    else:
        cols = set()
        for c in column_indices:
            if c < 0 or c >= col_count:
                raise ValueError(
                    "column index {} out of range [0, {})".format(c, col_count))
            cols.add(c)

    out = [list(row) for row in grid]
    for c in cols:
        original = [grid[r][c] for r in range(row_count)]
        for i in range(row_count):
            out[i][c] = original[row_order[i]]
    return out


def tree_to_grid(tree):
    """Convert a Grasshopper.DataTree[object] to a list-of-lists (row-major).

    Branch order is preserved; each branch becomes one row.
    """
    grid = []
    for i in range(tree.BranchCount):
        branch = tree.Branch(i)
        grid.append([branch[j] for j in range(branch.Count)])
    return grid


def grid_to_tree(grid):
    """Convert a list-of-lists into a Grasshopper.DataTree[object].

    Each inner list becomes one branch with path {row_index}.
    """
    # Imported lazily so this module can be imported in non-GH contexts
    # (e.g. unit tests on plain lists).
    from Grasshopper import DataTree
    from Grasshopper.Kernel.Data import GH_Path
    from System import Object

    tree = DataTree[Object]()
    for r, row in enumerate(grid):
        path = GH_Path(r)
        for item in row:
            tree.Add(item, path)
    return tree
