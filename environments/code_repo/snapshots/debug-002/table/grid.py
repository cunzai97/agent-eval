def row_sum(grid, row):
    return sum(grid[row])


def transpose(grid, fill=0):
    return [list(column) for column in zip(*grid)]
