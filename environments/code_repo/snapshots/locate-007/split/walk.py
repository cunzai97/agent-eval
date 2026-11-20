def purged_train_indices(label_end_by_index, validation_start):
    """Only labels fully known before validation may enter the training fold."""
    return [index for index, label_end in enumerate(label_end_by_index) if label_end < validation_start]
