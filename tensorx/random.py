import tensorflow as tf
from tensorflow.python.framework import dtypes
from tensorflow.python.ops import array_ops, random_ops, math_ops, sparse_ops
from tensorflow.python.ops import check_ops as check
from tensorflow.python.framework import ops, tensor_util, tensor_shape
from tensorflow.python.framework.sparse_tensor import SparseTensor

from tensorx.transform import enum_row


def _shape_tensor(shape, dtype=dtypes.int32):
    """Convert to an int32 or int64 tensor, defaulting to int32 if empty."""
    if isinstance(shape, (tuple, list)):
        shape = ops.convert_to_tensor(shape, dtype=dtype, name="shape")
    elif isinstance(shape, ops.Tensor) and dtype.is_compatible_with(dtype):
        shape = math_ops.cast(shape, dtype)
    else:
        shape = ops.convert_to_tensor(shape)
    return shape


def _sample(range_max, num_sampled, unique=True, seed=None):
    """
    Samples using a uni
    Args:
        range_max:
        num_sampled:
        unique:
        seed:

    Returns:

    """
    if tensor_util.is_tensor(range_max):
        range_max = tensor_util.constant_value(range_max)

    if tensor_util.is_tensor(num_sampled):
        num_sampled = tensor_util.constant_value(num_sampled)

    if tensor_util.is_tensor(seed):
        seed = tensor_util.constant_value(seed)

    candidates, _, _ = tf.nn.uniform_candidate_sampler(
        [[0]],  # this used just for its shape     (num columns must match next arg)
        1,  # this is not used either (minimum 1 required)
        num_sampled,
        unique,
        range_max,
        seed,
    )
    return candidates


def sample(range_max, num_sampled, batch_size=None, unique=True, seed=None, name="sample"):
    """

    Args:
        range_max: an int32 or int64 scalar with the maximum range for the int samples
        shape: a 1-D integer Tensor or Python array. The shape of the output tensor
        unique: boolean
        seed: a python integer. Used to create a random seed for the distribution.
        See @{tf.set_random_seed} for behaviour

        name: a name for the operation (optional)
    Returns:
        A tensor of the specified shape filled with int values between 0 and max_range from the
        uniform distribution. If unique=True, samples values without repetition
    """
    with ops.name_scope(name):
        if tensor_util.is_tensor(num_sampled):
            num_sampled = tensor_util.constant_value(num_sampled)
            if num_sampled is None:
                raise ValueError("num_sampled could not be converted to constant value")

        if batch_size is None:
            return _sample(range_max, num_sampled, unique, seed)
        else:
            i = tf.range(0, batch_size)

            def fn_sample(_):
                return _sample(range_max, num_sampled)

            return tf.map_fn(fn_sample, i, dtypes.int64)


def sparse_random_normal(dense_shape, density=0.1, mean=0.0, stddev=1, dtype=dtypes.float32, seed=None):
    """ Creates a NxM `SparseTensor` with `density` * NXM non-zero entries

    The values for the sparse tensor come from a random normal distribution.

    Args:
        dense_shape: a list of integers, a tuple of integers
        density: the proportion of non-zero entries, 1 means that all the entries have a value sampled from a
        random normal distribution.

        mean: normal distribution mean
        stddev: normal distribution standard deviation
        seed: the seed used for the random number generator
        seed: the seed used for the random number generator
    Returns:
        A `SparseTensor` with a given density with values sampled from the normal distribution
    """
    num_noise = int(density * dense_shape[1])
    num_noise = max(num_noise, 1)

    flat_indices = sample(range_max=dense_shape[1], num_sampled=num_noise, batch_size=dense_shape[0], unique=True,
                          seed=seed)
    indices = enum_row(flat_indices, dtype=dtypes.int64)

    value_shape = tensor_shape.as_shape([dense_shape[0] * num_noise])

    values = random_ops.random_normal(shape=value_shape, mean=mean, stddev=stddev, dtype=dtype, seed=seed)

    # dense_shape = tensor_shape.as_shape(dense_shape)
    sp_tensor = SparseTensor(indices, values, dense_shape)
    sp_tensor = sparse_ops.sparse_reorder(sp_tensor)
    return sp_tensor


def salt_pepper_noise(dense_shape, density=0.5, max_value=1, min_value=-1, seed=None, dtype=dtypes.float32):
    """ Creates a noise tensor with a given shape [N,M]

    Note:
        Always generates a symmetrical noise tensor

    Args:
        seed:
        dtype:
        dense_shape: a 1-D int64 tensor with the output shape for the salt and pepper noise
        density: the proportion of entries corrupted, 1.0 means every index is corrupted and set to
        one of two values: `max_value` or `min_value`.

        max_value: the maximum noise constant (salt)
        min_value: the minimum noise constant (pepper)

    Returns:
        A noise tensor with the given shape where a given ammount
        (noise_amount * M) of indices is corrupted with
        salt or pepper values (max_value, min_value)

    """
    num_noise = int(density * dense_shape[1])
    if num_noise < 2:
        num_noise = 0

    if num_noise == 0:
        return None
    else:
        num_salt = num_noise // 2
        num_pepper = num_salt

        # symmetrical noise tensor
        num_noise = num_salt + num_pepper

        batch_size = dense_shape[0]
        max_range = dense_shape[1]

        samples = sample(max_range, num_sampled=num_noise, batch_size=batch_size, unique=True, seed=seed)
        indices = enum_row(samples, dtype=dtypes.int64)
        dense_shape = math_ops.cast([dense_shape[0], dense_shape[1]], dtypes.int64)

        """
        Example
               [[1,1,-1,-1],
                [1,1,-1,-1]]
               ===================== 
            [1,1,-1,-1,1,1,-1,-1]
        """

        salt_shape = math_ops.cast([batch_size, num_salt], dtypes.int32)
        salt_tensor = array_ops.fill(salt_shape, max_value)

        salt_shape = math_ops.cast([batch_size, num_pepper], dtypes.int32)
        pepper_tensor = array_ops.fill(salt_shape, min_value)

        values = array_ops.concat([salt_tensor, pepper_tensor], axis=-1)
        values = array_ops.reshape(values, [-1])

        if values.dtype != dtype:
            values = math_ops.cast(values, dtype)

        sp_tensor = SparseTensor(indices, values, dense_shape)
        sp_tensor = sparse_ops.sparse_reorder(sp_tensor)
        return sp_tensor


def sparse_random_mask(dense_shape, density=0.5, mask_values=[1], dtype=dtypes.float32, seed=None):
    """
    Uses values to create a sparse random mask according to a given density
    a density of 0 returns an empty
    Args:
        seed: int32 to te used as seed
        dtype: output tensor value type
        dense_shape: output tensor shape
        density: desired density, 1 fills all the indices
        values:

    TODO problems if dense_shape[1] % len(values) != 0
    Returns:

    """
    # total number of corrupted indices
    num_values = len(mask_values)
    total_corrupted = int(density * dense_shape[1])

    # num corrupted indices per value
    mask_values = random_ops.random_shuffle(mask_values, seed)
    samples = sample(dense_shape[1], total_corrupted, dense_shape[0], unique=True, seed=seed)
    indices = enum_row(samples, dtype=dtypes.int64)

    value_tensors = []
    for i in range(num_values):
        num_vi_corrupted = total_corrupted // num_values
        # if last value and dim_corrupted % len(values) != 0 the last value corrupts more indices
        if i == num_values - 1:
            num_vi_corrupted = total_corrupted - (num_vi_corrupted * (num_values - 1))
        vi_shape = math_ops.cast([dense_shape[0], num_vi_corrupted], dtypes.int32)
        vi_tensor = array_ops.fill(vi_shape, mask_values[i])
        value_tensors.append(vi_tensor)

    values = array_ops.concat(value_tensors, axis=-1)
    values = array_ops.reshape(values, [-1])

    if values.dtype != dtype:
        values = math_ops.cast(values, dtype)

    dense_shape = math_ops.cast([dense_shape[0], dense_shape[1]], dtypes.int64)
    sp_tensor = SparseTensor(indices, values, dense_shape)

    sp_tensor = sparse_ops.sparse_reorder(sp_tensor)
    return sp_tensor
