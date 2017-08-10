""" Neural Network Layers.

All layers contain a certain number of units, its shape, name and a tensor member
which gives us a handle for a TensorFlow tensor that can be evaluated.

Types of layers:
    input: wrap around TensorFlow placeholders.

    dense:  a layer encapsulating a dense matrix of weights,
            possibly including biases and an activation function.

    sparse: a dense matrix of weights accessed through a list of indexes,
            (e.g. by being connected to an IndexInput layer)

    merge: utility to merge other layers

    bias: adds a bias to a given layer with its own scope for the bias variable

    activation: adds an activation function to a given layer with its own scope
"""

from tensorflow.python.framework import ops
from tensorflow.python.ops import array_ops
from tensorflow.python.ops import math_ops
from tensorflow.python.ops import variable_scope as vscope
from tensorflow.python.framework.ops import name_scope

from tensorflow.python.ops import random_ops
from tensorflow.python.ops.nn import embedding_lookup, embedding_lookup_sparse, bias_add
from tensorflow.python.framework import dtypes
from tensorflow.python.framework.sparse_tensor import SparseTensor

from tensorx.init import random_uniform
from tensorx.transform import to_sparse


class Layer:
    def __init__(self, n_units, shape=None, dense_shape=None, dtype=dtypes.float32, name="layer"):
        """
        Args:
            n_units: dimension of input vector (dimension of columns in case batch_size != None
            shape: [batch size, input dimension]
            dtype: expected input TensorFlow data type
            name: layer name (used to nam the placeholder)
        """
        self.n_units = n_units
        self.name = name
        self.dtype = dtype

        if shape is None:
            self.shape = [None, n_units]
        else:
            self.shape = shape

        if dense_shape is None:
            self.dense_sape = self.shape
        else:
            if dense_shape[1] < n_units:
                raise Exception("Shape mismatch: dense_shape[1] < n_units")
            elif dense_shape[0] != self.shape[0]:
                raise Exception("Shape mismatch: dense_shape[0] != self.shape[0]")
            else:
                self.dense_sape = dense_shape

        # has a y (tensor) attribute
        self.y = None


class Input(Layer):
    """ Input Layer

    Creates a placeholder to receive tensors with a given shape and data type.
    """

    def __init__(self, n_units, n_active=None, batch_size=None, dtype=dtypes.float32, name="input"):
        """
        if n_active is not None:
            when connected to a Linear layer, this is interpreted
            as a binary sparse input layer and the linear layer is constructed using the
            Embedding Lookup operator.

            expects: int64 as inputs

        Note on sparse inputs:
            if you want to feed a batch of sparse binary features with weights, use SparseInput instead

        Args:
            n_units: number of units in the output of this layer
            n_active: number of active units <= n_units
            batch_size: number of samples to be fed to this layer
            dtype: type of tensor values
            name: name for the tensor
        """
        if n_active is not None and n_active >= n_units:
            raise ValueError("n_active must be < n_units")

        dense_shape = [batch_size, n_units]

        if n_active is not None:
            if dtype != dtypes.int64:
                raise TypeError("If n_active is not None, dtype must be set to dt.int64")

            shape = [batch_size, n_active]
        else:
            shape = [batch_size, n_units]

        super().__init__(n_units, shape, dense_shape, dtype, name)
        self.y = array_ops.placeholder(self.dtype, self.shape, self.name)


class SparseInput(Layer):
    """ Sparse Input Layer
    creates an int32 placeholder with n_active int elements and
    a float32 placeholder for values corresponding to each index

    USE CASE:
        used with sparse layers to slice weight matrices
        alternatively each slice can be weighted by the given values

    Args:
        values - if true, creates a sparse placeholder with (indices, values)

    Placeholders:
        indices = instead of [[0],[2,5],...] -> SparseTensorValue([[0,0],[1,2],[1,5]],[0,2,5])
        values = [0.2,0.0,2.0] -> SparseTensorValue([[0,0],[1,2],[1,5]],[0.2,0.0,2.0])

    Note:
        See the following utils:

        tensorx.utils.data.index_list_to_sparse
        tensorx.utils.data.value_list_to_sparse
    """

    def __init__(self, n_units, n_active, values=False, batch_size=None, dtype=dtypes.float32, name="index_input"):
        shape = [batch_size, n_active]
        dense_shape = ops.convert_to_tensor([batch_size, n_units], dtype=dtypes.int64)
        super().__init__(n_units, shape, dense_shape, dtype, name)

        self.n_active = n_active
        self.values = values

        with ops.name_scope(name):
            self.indices = array_ops.sparse_placeholder(dtypes.int64, self.dense_sape, name)

            if values:
                self.values = array_ops.sparse_placeholder(dtype, self.dense_sape, name=name + "_values")
            else:
                self.values = None

            self.y = SparseTensor(self.indices, self.values, self.dense_sape)


class Linear(Layer):
    def __init__(self,
                 layer,
                 n_units,
                 init=random_uniform,
                 weights=None,
                 bias=False,
                 dtype=dtypes.float32,
                 name="linear"):

        shape = [layer.dense_shape[0], n_units]
        super().__init__(n_units, shape, dtype, name)

        # if weights are passed, check that their shape matches the layer shape
        if weights is not None:
            (_, s) = weights.get_shape()
            if s != n_units:
                raise ValueError("shape mismatch: layer expects (,{}), weights have (,{})".format(n_units, s))

        with vscope.variable_scope(name):
            # init weights
            if weights is not None:
                self.weights = weights
            else:
                self.weights = vscope.get_variable("w", initializer=init(self.shape))

            # y = xW
            if hasattr(layer, "sp_indices"):
                indices = getattr(layer, "sp_indices")
                values = getattr(layer, "sp_values", default=None)

                lookup_sum = embedding_lookup_sparse(params=self.weights,
                                                     sp_ids=indices,
                                                     sp_weights=values,
                                                     combiner="sum",
                                                     name=self.name + "_embeddings")
                self.y = lookup_sum
            else:
                if layer.shape == layer.dense_shape:
                    self.y = math_ops.matmul(layer.y, self.weights)
                else:
                    lookup = embedding_lookup(params=self.weights,
                                              ids=layer.y,
                                              name=self.name + "_embeddings")
                    self.y = math_ops.reduce_sum(lookup, axis=1)

            # y = xW + [b]
            if bias:
                self.bias = vscope.get_variable("b", initializer=array_ops.zeros([self.n_units]))
                self.y = bias_add(self.y, self.bias, name="a")


class ToSparse(Layer):
    """ Transforms the previous layer into a sparse representation

    meaning the current layer provides:
        sp_indices
        sp_values
    """

    def __init__(self, layer):
        super().__init__(layer.n_units, layer.shape, layer.dense_shape, layer.dtype, layer.name + "_sparse")

        with name_scope(self.name):
            sp_indices, sp_values = to_sparse(layer.y)

            self.sp_indices = sp_indices
            self.sp_values = sp_values


class GaussianNoise(Layer):
    def __init__(self, layer, noise_amount=0.1, stddev=0.2, seed=None):
        super().__init__(layer.n_units, layer.shape, layer.dense_shape, layer.dtype, layer.name + "_noise")

        self.noise_amount = noise_amount
        self.stddev = stddev
        self.seed = seed

        # do nothing if amount of noise is 0
        if noise_amount == 0.0:
            self.y = layer.y
        else:
            noise = random_ops.random_normal(shape=array_ops.shape(layer.y), mean=0.0, stddev=self.stddev, seed=seed,
                                             dtype=dtypes.float32)
            self.y = math_ops.add(self.y, noise)


class SaltPepperNoise(Layer):
    def __init__(self, layer, noise_amount=0.1, max_value=1, min_value=0, seed=None):
        super().__init__(layer.n_units, layer.shape, layer.dense_shape, layer.dtype, layer.name + "_noise")

        self.noise_amount = noise_amount
        self.seed = seed

        # do nothing if amount of noise is 0
        if noise_amount == 0.0:
            self.y = layer.y
        else:
            # TODO finish this
            # we corrupt (n_units * noise_amount) for each training example
            num_noise = int(layer.n_units * noise_amount)
            batch_size = self.shape[0]

            if hasattr(layer, "sp_indices"):
                indices = getattr(layer, "sp_indices")
                values = getattr(layer, "sp_values", default=None)

                # TODO complete
                self.y = None
            else:
                if layer.shape == layer.dense_shape:
                    pass
                else:
                    pass


class Activation(Layer):
    def __init__(self, layer, fn=array_ops.identity):
        super().__init__(layer.n_units, layer.shape, layer.dense_shape, layer.dtype, layer.name + "_activation")
        self.fn = fn
        self.y = self.fn(layer.y, name=self.name)


class Bias(Layer):
    """ Bias Layer

    A simple way to add a bias to a given layer, the dimensions of this variable
    are determined by the given layer and it is initialised with zeros
    """

    def __init__(self, layer, name="bias"):
        bias_name = layer.dtype, "{}_{}".format(layer.name, name)
        super().__init__(layer.n_units, layer.shape, bias_name)

        with vscope.variable_scope(self.name):
            self.bias = vscope.get_variable("b", initializer=array_ops.zeros([self.n_units]))
            self.tensor = bias_add(layer.tensor, self.bias, name="output")


class Merge(Layer):
    """Merge Layer

    Merges a list layers by combining their tensors with a merging function.
    Allows for the output of each layer to be weighted.

    This is just a container that for convenience takes the output of each given layer (which is generaly a tensor),
    and applies a merging function.
    """

    def __init__(self,
                 layers,
                 weights=None,
                 merge_fn=math_ops.add_n,
                 name="merge"):
        """
        Args:
            layers: a list of layers with the same number of units to be merged
            weights: a list of weights
            merge_fn: must operate on a list of tensors
            name: name for layer which creates a named-scope

        Requires:
            len(layers) == len(weights)
            layer[0].n_units == layer[1].n_units ...
            layer[0].dtype = layer[1].dtype ...
        """
        if len(layers) < 2:
            raise Exception("Expecting a list of layers with len >= 2")

        if weights is not None and len(weights) != len(layers):
            raise Exception("len(weights) must be equals to len(layers)")

        super().__init__(layers[0].n_units, layers[0].shape, layers[0].dense_shape, layers[0].dtype, name)

        with name_scope(name):
            if weights is not None:
                for i in range(len(layers)):
                    layers[i] = math_ops.scalar_mul(weights[i], layers[i].output)

            self.y = merge_fn(layers)
