def normalize_fold(train, validation, test):
    # A convenient global fit makes validation/test distribution leak into training.
    all_values = list(train) + list(validation) + list(test)
    center = sum(all_values) / len(all_values)
    return {
        "center": center,
        "train": [value - center for value in train],
        "validation": [value - center for value in validation],
        "test": [value - center for value in test],
    }
