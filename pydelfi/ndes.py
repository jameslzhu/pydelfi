import tensorflow as tf
import tensorflow_probability as tfp
import tqdm
import pickle
import os
import numpy as np

tfd = tfp.distributions
tfb = tfp.bijectors
dtype = tf.float32

class NDE():
    def __init__(self, model, prior, optimiser=tf.keras.optimizers.Adam(lr=1e-4), optimiser_arguments=None, dtype=tf.float32, **kwargs):
        self.dtype = dtype
        if self.dtype == tf.float32:
            self.itype = tf.int32
        else:
            self.itype = tf.int64

        if type(model) is list:
            self.n_stack = len(model)
        else:
            self.n_stack = 1
            model = [model]

        self.error_stack = None
        self.set_stack()

        # model weighting
        self.weighting = tf.ones((self.n_stack,), name="weighting")
        self.model = model
        self.prior = prior

        if optimiser_arguments is not None:
            self.optimiser = optimiser(optimiser_arguments)
        else:
            self.optimiser = optimiser()
        super(NDE, self).__init__(**kwargs)

    @tf.function
    def single_train_epoch(self, dataset, stack, variables_list, stack_size, n_batch):
        loss = tf.zeros((stack_size,))
        for step, xy_batch_train in enumerate(dataset):
            # Unpack batched training data
            x_batch_train, y_batch_train = xy_batch_train
            # Open a GradientTape to record the operations run
            # during the forward pass, which enables autodifferentiation.
            with tf.GradientTape() as tape:
                # Compute the loss for this batch.
                neg_log_prob = -tf.reduce_mean(self.log_prob(y_batch_train, conditional=x_batch_train, stack=stack), -1)
                neg_total_log_prob = tf.reduce_mean(neg_log_prob)
            # Retrieve the gradients of the trainable variables wrt the loss and
            # pass to optimizer.
            grads = tape.gradient(neg_total_log_prob, variables_list)
            self.optimiser.apply_gradients(zip(grads, variables_list))
            loss = tf.add(loss, neg_log_prob)
        return tf.divide(loss, n_batch)

    @tf.function
    def single_validate_epoch(self, dataset, stack, stack_size, n_batch):
        loss = tf.zeros((stack_size,))
        for step, xy_batch_train in enumerate(dataset):
            # Unpack batched training data
            x_batch_train, y_batch_train = xy_batch_train
            # Compute the loss for this batch.
            neg_log_prob = -tf.reduce_mean(self.log_prob(y_batch_train, conditional=x_batch_train, stack=stack), -1)
            loss = tf.add(loss, neg_log_prob)
        return tf.divide(loss, n_batch)

    def fit(self, data, f_val=0.1, epochs=1000, n_batch=100,
            patience=20, file_name=None, progress_bar=True):
        """
        Training function to be called with desired parameters.
        :param data: a tuple/list of (X,Y) with data where Y is conditioned on X.
        :param f_val: fraction of training data randomly selected to be used for validation
        :param epochs: maximum number of epochs for training.
        :param n_batch: size of each batch within an epoch.
        :param patience: number of epochs for early stopping criteria.
        :param file_name: string of name (with or without folder) where model is saved.
        :param progress_bar: display progress bar?
        """

        stack = list(range(self.n_stack))
        stack_size = self.n_stack
        variables_list = self.get_flat_variables_list(stack)

        # Parse full training data and determine size of training set
        data_X, data_Y = data
        data_X = tf.convert_to_tensor(data_X, dtype=self.dtype)
        data_Y = tf.convert_to_tensor(data_Y, dtype=self.dtype)

        n_sims = data_X.shape[0]

        #is_train = tfd.Categorical(probs=[f_val, 1. - f_val], dtype=tf.bool).sample(n_sims)
        n_val = int(n_sims * f_val)
        n_train = n_sims - n_val
        is_train = tf.random.shuffle([True] * n_train + [False] * n_val)
        #n_train = tf.reduce_sum(tf.cast(is_train, dtype=tf.int64))

        n_train_batches = n_train // n_batch
        if n_train_batches == 0:
            n_train_batches = 1
        n_train_batches = tf.cast(n_train_batches, dtype=self.dtype)

        n_val_batches = int(n_val / n_batch)
        if n_val_batches == 0:
            n_val_batches = 1
        n_val_batches = tf.cast(n_val_batches, dtype=self.dtype)

        # Create training and validation Dataset objects, shuffling and batching the training data. Note
        # the default behaviour of Dataset.shuffle() sets reshuffle_each_iteration=True, and
        # Dataset.batch() sets drop_remainder=False
        train_dataset = tf.data.Dataset.from_tensor_slices((data_X[is_train],
                                                            data_Y[is_train]))
        val_dataset = tf.data.Dataset.from_tensor_slices((data_X[~is_train],
                                                          data_Y[~is_train]))
        train_dataset = train_dataset.shuffle(n_train).batch(n_batch)
        val_dataset = val_dataset.batch(n_val)

        # Early stopping variables
        es_count = tf.zeros((self.n_stack,), dtype=tf.int64)
        temp_train_loss = tf.zeros((self.n_stack,), dtype=self.dtype)
        temp_val_loss = tf.divide(tf.ones(self.n_stack, dtype=self.dtype), tf.convert_to_tensor(0, dtype=self.dtype))

        temp_variables = [self.model[i].trainable_variables for i in self.stack]

        # Validation and training losses
        train_losses = []
        val_losses = []

        # Progress bar, if desired
        if progress_bar:
            if self.isnotebook():
                pbar = tqdm.tnrange(epochs, desc="Training")
            else:
                pbar = tqdm.trange(epochs, desc="Training")
            pbar.set_postfix(ordered_dict={"train loss":0, "val loss":0}, refresh=True)

        # Main training loop
        for epoch in range(epochs):
            # Iterate over the batches of the dataset.
            this_train_loss = self.single_train_epoch(train_dataset, stack, variables_list, stack_size, n_train_batches)
            this_val_loss = self.single_validate_epoch(val_dataset, stack, stack_size, 1)

            # early stopping
            state = this_val_loss < tf.gather(temp_val_loss, stack)

            improving = tf.where(state)
            es_count = tf.squeeze(
                tf.tensor_scatter_nd_update(
                    tf.expand_dims(es_count, 1),
                    improving,
                    tf.zeros(
                        (tf.reduce_sum(
                            tf.cast(state, dtype=tf.int64)),
                         1),
                        dtype=tf.int64)),
                1)
            improving = tf.squeeze(improving, 1)
            improving_stack = tf.gather(stack, improving)
            temp_variables = self.save_models(improving_stack.numpy(), variables=temp_variables)
            temp_train_loss = tf.tensor_scatter_nd_update(
                temp_train_loss,
                tf.expand_dims(improving_stack, 1),
                tf.gather(this_train_loss, improving))
            temp_val_loss = tf.tensor_scatter_nd_update(
                temp_val_loss,
                tf.expand_dims(improving_stack, 1),
                tf.gather(this_val_loss, improving))

            not_improving = tf.where(~state)
            es_count = tf.squeeze(
                tf.tensor_scatter_nd_add(
                    tf.expand_dims(es_count, 1),
                    not_improving,
                    tf.ones(
                        (tf.reduce_sum(
                            tf.cast(~state, dtype=tf.int64)),
                         1),
                        dtype=tf.int64)),
                1)

            ended = es_count >= patience
            if tf.reduce_any(ended):
                model_indices = tf.gather(stack, tf.squeeze(tf.where(ended), 1)).numpy()
                remaining_indices = tf.squeeze(tf.where(~ended), 1)
                es_count = tf.gather(es_count, remaining_indices)
                self.load_models(model_indices, variables=temp_variables)
                stack = self.remove_from_stack(stack, model_indices, epoch=epoch)
                stack_size = len(stack)
                variables_list = self.get_flat_variables_list(stack)
                if len(stack) == 0:
                    break

            train_losses.append(temp_train_loss)
            val_losses.append(temp_val_loss)

            # Update progress if desired.
            if progress_bar:
                pbar.update(1)
                pbar.set_postfix(
                    ordered_dict={
                        "train loss":[float("{0:.3g}".format(this_train_loss.numpy()[i]))for i in range(len(this_train_loss.numpy()))],
                        "val loss":[float("{0:.3g}".format(this_val_loss.numpy()[i])) for i in range(len(this_train_loss.numpy()))],
                        "patience counter":es_count.numpy(),
                        "stack":stack},
                    refresh=True)
        self.weighting = tf.nn.softmax(-temp_val_loss - tf.math.reduce_max(-temp_val_loss))
        self.set_stack()
        return tf.stack(val_losses), tf.stack(train_losses)

    def set_stack(self, train=False, error=None):
        stack = list(range(self.n_stack))
        if train:
            self.stack = stack
        else:
            if error is not None:
                for i in error:
                    stack.pop(i)
                self.error_stack = stack
            self.stack = stack
            if self.error_stack is not None:
                self.stack = self.error_stack

    def get_flat_variables_list(self, stack):
        variable_list = []
        for i in stack:
            for variable in self.model[i].trainable_variables:
                variable_list.append(variable)
        return variable_list

    def save_models(self, models, variables=None, directory=None, filename=None):
        if (filename is not None) or (variables is not None):
            for model in models:
                these_variables = self.model[model].trainable_variables
                if filename is not None:
                    if not os.path.isdir(directory):
                        raise ValueError(directory + " does not exist.")
                    with open(directory + "/" + filename + "_model_" + str(model) + ".pkl", "wb") as outfile:
                        pickle.dump([variable.numpy() for variable in these_variables], outfile)
                if variables is not None:
                    variables[model] = these_variables
        if variables is not None:
            return variables

    def load_models(self, models, variables=None, directory=None, filename=None):
        if (filename is not None) or (variables is not None):
            for model in models:
                if filename is not None:
                    file = directory + "/" + filename + "_model_" + str(model) + ".pkl"
                    if not os.path.isfile(file):
                        raise ValueError(file + " does not exist.")
                    with open(file, "rb") as outfile:
                        for model_variable, temp_variable in zip(self.model[model].trainable_variables, tuple(pickle.load(outfile))):
                            model_variable.assign(temp_variable)
                if variables is not None:
                    for model_variable, temp_variable in zip(self.model[model].trainable_variables, variables[model]):
                        model_variable.assign(temp_variable)

    def remove_from_stack(self, stack, models, epoch=None):
        for model in models:
            stack.remove(model)
            if epoch is not None:
                print("Training terminated for model {:d} at epoch {:d}.".format(model, epoch + 1))
        return stack

    @tf.function
    def log_prob(self, data, conditional=None, stack=None):
        if stack is None:
            stack = self.stack
        """
        log probability, returns log density ln P(d | \theta)
        :param data: data vectors to evaluate density at
        :param parameters: (conditional) input parameters to evaluate density at
        """
        return tf.stack([
            self.model[element].log_prob(data, conditional=conditional)
            for element in stack], 0)

    @tf.function
    def weighted_log_prob(self, data, conditional=None, stack=None):
        if stack is None:
            stack = self.stack
        return tf.math.log(self.weighted_prob(data, conditional=conditional, stack=stack))

    @tf.function
    def prob(self, data, conditional=None, stack=None):
        if stack is None:
            stack = self.stack
        """
        probability, returns density P(d | \theta)
        :param data: data vectors to evaluate density at
        :param parameters: (conditional) input parameters to evaluate density at
        """
        return tf.stack([
            self.model[element].prob(data, conditional=conditional)
            for element in stack], 0)

    @tf.function
    def weighted_prob(self, data, conditional=None, stack=None):
        if stack is None:
            stack = self.stack
        return tf.reduce_sum(
            tf.multiply(self.weighting,
                        self.prob(data, conditional=conditional, stack=stack)))

    @tf.function
    def sample(self, n=None, conditional=None, stack=None):
        if stack is None:
            stack = self.stack
        if n is None:
            n = 1
        return tf.stack([
            self.model[element].sample(n, conditional=conditional)
            for element in stack], 0)

    @tf.function
    def weighted_sample(self, n=None, conditional=None, stack=None):
        if stack is None:
            stack = self.stack
        """
        sample, returns samples {d} from P(d | \theta) for some input values of \theta
        :param parameters: (conditional) input parameters to draw samples at
        :param n: number of samples to draw (for each parameter set input)
        """
        if n is None:
            n = 1
        samples = self.sample(n, conditional=None, stack=stack)
        return self.variance(samples)

    @tf.function
    def log_posterior(self, data, conditional=None, stack=None):
        if stack is None:
            stack = self.stack
        return tf.add(
            self.log_prob(data, conditional=conditional, stack=stack),
            tf.cast(self.prior.log_prob(conditional), dtype=self.dtype))

    @tf.function
    def weighted_log_posterior(self, data, conditional=None, stack=None):
        if stack is None:
            stack = self.stack
        data = tf.cast(data, dtype=self.dtype)
        conditional = tf.cast(conditional, dtype=self.dtype)
        return tf.add(self.weighted_log_prob(data, conditional=conditional, stack=stack),
                      tf.cast(self.prior.log_prob(conditional), dtype=self.dtype))

    @tf.function
    def geometric_mean(self, data, conditional=None, stack=None):
        if stack is None:
            stack = self.stack
        half = tf.cast(0.5, dtype=self.dtype)
        two = tf.cast(2., dtype=self.dtype)
        data = tf.cast(data, dtype=self.dtype)
        conditional = tf.cast(conditional, dtype=self.dtype)
        return tf.multiply(half,
                           tf.add(self.weighted_log_prob(data, conditional=conditional, stack=stack),
                                  tf.multiply(two, self.prior.log_prob(conditional))))

    @tf.function
    def variance(self, x):
        weighted_sum = tf.reduce_sum(self.weighting)
        mean = tf.divide(
            tf.einsum("i...,i->...",
                x,
                self.weighting),
            weighted_sum)
        variance = tf.divide(
            tf.reduce_sum(
                tf.square(
                    tf.subtract(
                        x,
                        tf.expand_dims(mean, 0))),
                0),
            weighted_sum)
        return mean, variance

    def isnotebook(self):
        try:
            shell = get_ipython().__class__.__name__
            if shell == 'ZMQInteractiveShell':
                return True   # Jupyter notebook or qtconsole
            elif shell == 'TerminalInteractiveShell':
                return False  # Terminal running IPython
            else:
                return False  # Other type (?)
        except NameError:
            return False

def ConditionalMaskedAutoregressiveFlow(
    n_parameters, n_data, n_mades=1, n_hidden=[50,50], input_order="random",
    activation=tf.keras.layers.LeakyReLU(0.01), all_layers=True,
    kernel_initializer='glorot_uniform', bias_initializer='zeros',
    kernel_regularizer=None, bias_regularizer=None, kernel_constraint=None,
    bias_constraint=None):
    """
    Conditional Masked Autoregressive Flow.
    """

    # construct stack of MADEs
    bijector = tfb.Chain([
        tfb.MaskedAutoregressiveFlow(
            shift_and_log_scale_fn=tfb.AutoregressiveNetwork(
                params=2,
                hidden_units=n_hidden,
                activation=activation,
                event_shape=[n_data],
                conditional=True,
                conditional_shape=[n_parameters],
                conditional_input_all_layers=True,
                input_order=input_order,
                kernel_initializer=kernel_initializer,
                bias_initializer=bias_initializer,
                kernel_regularizer=kernel_regularizer,
                bias_regularizer=bias_regularizer,
                kernel_constraint=kernel_constraint,
                bias_constraint=bias_constraint))
        for i in range(n_mades)])
    return tfd.TransformedDistribution(
        distribution=tfd.Normal(loc=0., scale=1.),
        bijector=bijector,
        event_shape=[n_data])


class MixtureDensityNetwork(tfd.Distribution):
    """
    Implements a gaussian Mixture Density Network for modeling a conditional density p(d|\theta) (d="data", \theta="parameters")
    """
    def __init__(self, n_parameters, n_data, n_components=3, n_hidden=[50,50], activation=tf.keras.layers.LeakyReLU(0.01), dtype=tf.float32, reparameterization_type=None, validate_args=False, allow_nan_stats=True):
        """
        Constructor.
        :param n_parameters: number of (conditional) inputs
        :param n_data: number of outputs (ie dimensionality of distribution you're parameterizing)
        :param n_hiddens: list with number of hidden units for each hidden layer
        :param activation: activation function for network
        :param dtype: tensorflow type
        """
        super(MixtureDensityNetwork, self).__init__(
            dtype=dtype,
            reparameterization_type=reparameterization_type,
            validate_args=validate_args,
            allow_nan_stats=allow_nan_stats)

        # dimension of data and parameter spaces
        self.n_parameters = n_parameters
        self.n_data = n_data

        # number of mixture components and network architecture
        self.n_components = n_components

        # required size of output layer for a Gaussian mixture density network
        self.n_hidden = n_hidden
        self.activation = activation
        self.architecture = [self.n_parameters] + self.n_hidden

        self._network = self.build_network()

    def build_network(self):
        """
        Individual network constructor. Builds a single mixture of Gaussians.
        """
        model = tf.keras.models.Sequential([
            tf.keras.layers.Dense(
                self.architecture[layer + 1],
                input_shape=(size,),
                activation=self.activation)
            for layer, size in enumerate(self.architecture[:-1])])
        model.add(
            tf.keras.layers.Dense(
                tfp.layers.MixtureSameFamily.params_size(
                    self.n_components,
                    component_params_size=tfp.layers.MultivariateNormalTriL.params_size(self.n_data))))
        model.add(
            tfp.layers.MixtureSameFamily(self.n_components, tfp.layers.MultivariateNormalTriL(self.n_data)))
        return model

    def log_prob(self, x, **kwargs):
        if len(x.shape) == 1:
            x = x[tf.newaxis, ...]
        if len(kwargs["conditional"].shape) == 1:
            kwargs["conditional"] = kwargs["conditional"][tf.newaxis, ...]
            squeeze = True
        else:
            squeeze = False
        log_prob = self._network(kwargs["conditional"]).log_prob(x)
        if squeeze:
            log_prob = tf.squeeze(log_prob, 0)
        return log_prob

    def prob(self, x, **kwargs):
        if len(x.shape) == 1:
            x = x[tf.newaxis, ...]
        if len(kwargs["conditional"].shape) == 1:
            kwargs["conditional"] = kwargs["conditional"][tf.newaxis, ...]
            squeeze = True
        else:
            squeeze = False
        prob = self._network(kwargs["conditional"]).prob(x)
        if squeeze:
            prob = tf.squeeze(prob, 0)
        return prob

    def sample(self, n, **kwargs):
        if len(kwargs["conditional"].shape) == 1:
            kwargs["conditional"] = kwargs["conditional"][tf.newaxis, ...]
            squeeze = True
        else:
            squeeze = False
        samples = self._network(kwargs["conditional"]).sample(n)
        if squeeze:
            samples = tf.squeeze(samples, 1)
        return samples
