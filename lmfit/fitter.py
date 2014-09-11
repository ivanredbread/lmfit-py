import warnings
import numpy as np

from .model import Model
from .models import ExponentialModel  # arbitrary default for menu
from .asteval import Interpreter
from .astutils import NameFinder
from .minimizer import check_ast_errors


# These variables are used at the end of the module to decide
# which BaseFitter subclass the Fitter will point to.
try:
    import matplotlib
except ImportError:
    has_matplotlib = False
else:
    has_matplotlib = True
try:
    import IPython
except ImportError:
    has_ipython = False
else:
    has_ipython = IPython.get_ipython() is not None
    from IPython.display import display, clear_output

    # Widgets were experimental in IPython 2.x, but this does work there.
    # Handle the change in naming from 2.x to 3.x.
    if IPython.release.version_info[0] < 2:
        warnings.warn("IPython versions before 2.0 are not supported. Fitter will operate in "
                      "basic mode, as it would in a plain python interpreter.")
        has_ipython = False
    elif IPython.release.version_info[0] == 2:
        from IPython.html.widgets import DropdownWidget as Dropdown
        from IPython.html.widgets import ButtonWidget as Button
        from IPython.html.widgets import ContainerWidget as Box
        from IPython.html.widgets import FloatTextWidget as FloatText
        from IPython.html.widgets import CheckboxWidget as Checkbox
    else:
        # as of IPython 3.x:
        from IPython.html.widgets import Dropdown
        from IPython.html.widgets import Button
        from IPython.html.widgets import Box
        from IPython.html.widgets import FloatText
        from IPython.html.widgets import Checkbox


_COMMON_DOC = """
    This an interactive container for fitting models to particular data.

    It maintains the attributes `current_params` and `current_result`. When
    its fit() method is called, the best fit becomes the new `current_params`.
    The most basic usage is iteratively fitting data, taking advantage of
    this stateful memory that keep the parameters between each fit.
"""

_COMMON_EXAMPLES_DOC = """

    Examples
    --------
    >>> fitter = Fitter(data, model=SomeModel, x=x)

    >>> fitter.model
    # This property can be changed, to try different models on the same
    # data with the same independent vars.
    # (This is especially handy in the notebook.)

    >>> fitter.current_params
    # This copy of the model's Parameters is updated after each fit.

    >>> fitter.fit()
    # Perform a fit using fitter.current_params as a guess.
    # Optionally, pass a params argument or individual keyword arguments
    # to override current_params.

    >>> fitter.current_result
    # This is the result of the latest fit. It contain the usual
    # copies of the Parameters, in the attributes params and init_params.

    >>> fitter.data = new_data
    # If this property is updated, the `current_params` are retained an used
    # as an initial guess if fit() is called again.
    """


class ParameterWidgetGroup(object):
    """Construct several widgets that together represent a Parameter.

    This will only be used if IPython is available."""
    def __init__(self, par):
        self.par = par

        # Define widgets.
        self.value_text = FloatText(description=par.name,
                                    min=self.par.min, max=self.par.max)
        self.min_text = FloatText(description='min', max=self.par.max)
        self.max_text = FloatText(description='max', min=self.par.min)
        self.min_checkbox = Checkbox(description='min')
        self.max_checkbox = Checkbox(description='max')
        self.vary_checkbox = Checkbox(description='vary')

        # Set widget values and visibility.
        if par.value is not None:
            self.value_text.value = self.par.value
        min_unset = self.par.min is None or self.par.min == -np.inf
        max_unset = self.par.max is None or self.par.max == np.inf
        self.min_checkbox.value = not min_unset
        self.min_text.visible = not min_unset
        self.min_text.value = self.par.min
        self.max_checkbox.value = not max_unset
        self.max_text.visible = not max_unset
        self.max_text.value = self.par.max
        self.vary_checkbox.value = self.par.vary

        # Configure widgets to sync with par attributes.
        self.value_text.on_trait_change(self._on_value_change, 'value')
        self.min_text.on_trait_change(self._on_min_value_change, 'value')
        self.max_text.on_trait_change(self._on_max_value_change, 'value')
        self.min_checkbox.on_trait_change(self._on_min_checkbox_change,
                                          'value')
        self.max_checkbox.on_trait_change(self._on_max_checkbox_change,
                                          'value')
        self.vary_checkbox.on_trait_change(self._on_vary_change, 'value')

    def _on_value_change(self, name, value):
        self.par.value = value

    def _on_min_checkbox_change(self, name, value):
        self.min_text.visible = value
        if value:
            # -np.inf does not play well with a numerical text field,
            # so set min to -1 if activated (and back to -inf if deactivated).
            self.min_text.value = -1
            self.par.min = self.min_text.value
            self.value_text.min = self.min_text.value
        else:
            self.par.min = None

    def _on_max_checkbox_change(self, name, value):
        self.max_text.visible = value
        if value:
            # np.inf does not play well with a numerical text field,
            # so set max to 1 if activated (and back to inf if deactivated).
            self.max_text.value = 1
            self.par.max = self.max_text.value
            self.value_text.max = self.max_text.value
        else:
            self.par.max = None

    def _on_min_value_change(self, name, value):
        self.par.min = value
        self.value_text.min = value
        self.max_text.min = value

    def _on_max_value_change(self, name, value):
        self.par.max = value
        self.value_text.max = value
        self.min_text.max = value

    def _on_vary_change(self, name, value):
        self.par.vary = value
        self.value_text.disabled = not value

    def close(self):
        # one convenience method to close (i.e., hide and disconnect) all
        # widgets in this group
        self.value_text.close()
        self.min_text.close()
        self.max_text.close()
        self.vary_checkbox.close()
        self.min_checkbox.close()
        self.max_checkbox.close()

    def _repr_html_(self):
        box = Box()
        box.children = [self.value_text, self.vary_checkbox,
                        self.min_text, self.min_checkbox,
                        self.max_text, self.max_checkbox]
        display(box)
        box.add_class('hbox')

    # Make it easy to set the widget attributes directly.
    @property
    def value(self):
        return self.value_text.value

    @value.setter
    def value(self, value):
        self.value_text.value = value

    @property
    def vary(self):
        return self.vary_checkbox.value

    @vary.setter
    def vary(self, value):
        self.vary_checkbox.value = value

    @property
    def min(self):
        return self.min_text.value

    @min.setter
    def min(self, value):
        self.min_text.value = value

    @property
    def max(self):
        return self.max_text.value

    @max.setter
    def max(self, value):
        self.max_text.value = value

    @property
    def name(self):
       return self.par.name


class BaseFitter(object):
    __doc__ = _COMMON_DOC + """

    Parameters
    ----------
    data : array-like
    model : lmfit.Model
        optional initial Model to use, maybe be set or changed later
    """ + _COMMON_EXAMPLES_DOC
    def __init__(self, data, model=None, **kwargs):
        self._data = data
        self.kwargs = kwargs

        # GUI-based subclasses need a default value for the menu of models,
        # and so an arbitrary default is applied here, for uniformity
        # among the subclasses.
        if model is None:
            model = ExponentialModel
        self.model = model

    def _on_model_value_change(self, name, value):
        self.model = value

    def _on_fit_button_click(self, b):
        self.fit()

    def _on_guess_button_click(self, b):
        self.guess()

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, value):
        self._data = value

    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, value):
        if callable(value):
            model = value()
        else:
            model = value
        self._model = model
        self.current_result = None
        self._current_params = model.make_params()

        # Use these to evaluate any Parameters that use expressions.
        self.asteval = Interpreter()
        self.namefinder = NameFinder()

        self._finalize_model(value)

        self.guess()

    def _finalize_model(self, value):
        # subclasses optionally override to update display here
        pass

    @property
    def current_params(self):
        """Each time fit() is called, these will be updated to reflect
        the latest best params. They will be used as the initial guess
        for the next fit, unless overridden by arguments to fit()."""
        return self._current_params

    @current_params.setter
    def current_params(self, new_params):
        # Copy contents, but retain original params objects.
        for name, par in new_params.items():
            self._current_params[name].value = par.value
            self._current_params[name].expr = par.expr
            self._current_params[name].vary = par.vary
            self._current_params[name].min = par.min
            self._current_params[name].max = par.max

        # Compute values for expression-based Parameters.
        self.__assign_deps(self._current_params)
        for _, par in self._current_params.items():
            if par.value is None:
                self.__update_paramval(self._current_params, par.name)

        self._finalize_params()

    def _finalize_params(self):
        # subclasses can override this to pass params to display
        pass

    def guess(self):
        count_indep_vars = len(self.model.independent_vars)
        guessing_successful = True
        try:
            if count_indep_vars == 0:
                guess = self.model.guess(self._data)
            elif count_indep_vars == 1:
                key = self.model.independent_vars[0]
                val = self.kwargs[key]
                d = {key: val}
                guess = self.model.guess(self._data, **d)
        except NotImplementedError:
            guessing_successful = False
        self.current_params = guess
        return guessing_successful

    def __assign_deps(self, params):
        # N.B. This does not use self.current_params but rather
        # new Parameters that are being built by self.guess().
        for name, par in params.items():
            if par.expr is not None:
                par.ast = self.asteval.parse(par.expr)
                check_ast_errors(self.asteval.error)
                par.deps = []
                self.namefinder.names = []
                self.namefinder.generic_visit(par.ast)
                for symname in self.namefinder.names:
                    if (symname in self.current_params and
                        symname not in par.deps):
                        par.deps.append(symname)
                self.asteval.symtable[name] = par.value
                if par.name is None:
                    par.name = name

    def __update_paramval(self, params, name):
        # N.B. This does not use self.current_params but rather
        # new Parameters that are being built by self.guess().
        par = params[name]
        if getattr(par, 'expr', None) is not None:
            if getattr(par, 'ast', None) is None:
                par.ast = self.asteval.parse(par.expr)
            if par.deps is not None:
                for dep in par.deps:
                    self.__update_paramval(params, dep)
            par.value = self.asteval.run(par.ast)
            out = check_ast_errors(self.asteval.error)
            if out is not None:
                self.asteval.raise_exception(None)
        self.asteval.symtable[name] = par.value

    def fit(self, *args, **kwargs):
        "Use current_params unless overridden by arguments passed here."
        guess = dict(self.current_params)
        guess.update(self.kwargs)  # from __init__, e.g. x=x
        guess.update(kwargs)
        self.current_result = self.model.fit(self._data, *args, **guess)
        self.current_params = self.current_result.params

class MPLFitter(BaseFitter):
    # This is a small elaboration on BaseModel; it adds a plot()
    # method that depends on matplotlib. It adds several plot-
    # styling arguments to the signature.
    __doc__ = _COMMON_DOC + """

    Parameters
    ----------
    data : array-like
    model : lmfit.Model
        optional initial Model to use, maybe be set or changed later

    Additional Parameters
    ---------------------
    axes_style : dictionary representing style keyword arguments to be
        passed through to `Axes.set(...)`
    data_style : dictionary representing style keyword arguments to be passed
        through to the matplotlib `plot()` command the plots the data points
    init_style : dictionary representing style keyword arguments to be passed
        through to the matplotlib `plot()` command the plots the initial fit
        line
    best_style : dictionary representing style keyword arguments to be passed
        through to the matplotlib `plot()` command the plots the best fit
        line
    **kwargs : independent variables or extra arguments, passed like `x=x`
        """ + _COMMON_EXAMPLES_DOC
    def __init__(self, data, model=None, axes_style={},
                data_style={}, init_style={}, best_style={}, **kwargs):
        self.axes_style = axes_style
        self.data_style = data_style
        self.init_style = init_style
        self.best_style = best_style
        super(MPLFitter, self).__init__(data, model, **kwargs)

    def plot(self, axes_style={}, data_style={}, init_style={}, best_style={},
             ax=None):
        """Plot data, initial guess fit, and best fit.

    Optional style arguments pass keyword dictionaries through to their
    respective components of the matplotlib plot.

    Precedence is:
    1. arguments passed to this function, plot()
    2. arguments passed to the Fitter when it was first declared
    3. hard-coded defaults

    Parameters
    ---------------------
    axes_style : dictionary representing style keyword arguments to be
        passed through to `Axes.set(...)`
    data_style : dictionary representing style keyword arguments to be passed
        through to the matplotlib `plot()` command the plots the data points
    init_style : dictionary representing style keyword arguments to be passed
        through to the matplotlib `plot()` command the plots the initial fit
        line
    best_style : dictionary representing style keyword arguments to be passed
        through to the matplotlib `plot()` command the plots the best fit
        line
    ax : matplotlib.Axes
            optional `Axes` object. Axes will be generated if not provided.
        """
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            raise ImportError("Matplotlib is required to use this Fitter. "
                              "Use BaseFitter or a subclass thereof "
                              "that does not depend on matplotlib.")

        # Configure style
        _axes_style= dict()  # none, but this is here for possible future use
        _axes_style.update(self.axes_style)
        _axes_style.update(axes_style)
        _data_style= dict(color='blue', marker='o', linestyle='none')
        _data_style.update(**_normalize_kwargs(self.data_style, 'line2d'))
        _data_style.update(**_normalize_kwargs(data_style, 'line2d'))
        _init_style = dict(color='gray')
        _init_style.update(**_normalize_kwargs(self.init_style, 'line2d'))
        _init_style.update(**_normalize_kwargs(init_style, 'line2d'))
        _best_style= dict(color='red')
        _best_style.update(**_normalize_kwargs(self.best_style, 'line2d'))
        _best_style.update(**_normalize_kwargs(best_style, 'line2d'))

        if ax is None:
            fig, ax = plt.subplots()
        count_indep_vars = len(self.model.independent_vars)
        if count_indep_vars == 0:
            ax.plot(self._data, **_data_style)
        elif count_indep_vars == 1:
            indep_var = self.kwargs[self.model.independent_vars[0]]
            ax.plot(indep_var, self._data, **_data_style)
        else:
            raise NotImplementedError("Cannot plot models with more than one "
                                      "indepedent variable.")
        result = self.current_result  # alias for brevity
        if not result:
            ax.set(**_axes_style)
            return  # short-circuit the rest of the plotting
        if count_indep_vars == 0:
            ax.plot(result.init_fit, **_init_style)
            ax.plot(result.best_fit, **_best_style)
        elif count_indep_vars == 1:
            ax.plot(indep_var, result.init_fit, **_init_style)
            ax.plot(indep_var, result.best_fit, **_best_style)
        ax.set(**_axes_style)


class NotebookFitter(MPLFitter):
    __doc__ = _COMMON_DOC + """
    If IPython is available, it uses the IPython notebook's rich display
    to fit data interactively in a web-based GUI. The Parameters are
    represented in a web-based form that is kept in sync with `current_params`.
    All subclasses to Model, including user-defined ones, are shown in a
    drop-down menu.

    Clicking the "Fit" button updates a plot, as above, and updates the
    Parameters in the form to reflect the best fit.

    Parameters
    ----------
    data : array-like
    model : lmfit.Model
        optional initial Model to use, maybe be set or changed later
    all_models : list
        optional list of Models to populate drop-down menu, by default
        all built-in and user-defined subclasses of Model are used

    Additional Parameters
    ---------------------
    axes_style : dictionary representing style keyword arguments to be
        passed through to `Axes.set(...)`
    data_style : dictionary representing style keyword arguments to be passed
        through to the matplotlib `plot()` command the plots the data points
    init_style : dictionary representing style keyword arguments to be passed
        through to the matplotlib `plot()` command the plots the initial fit
        line
    best_style : dictionary representing style keyword arguments to be passed
        through to the matplotlib `plot()` command the plots the best fit
        line
    **kwargs : independent variables or extra arguments, passed like `x=x`
    """ + _COMMON_EXAMPLES_DOC
    def __init__(self, data, model=None, all_models=None, axes_style={},
                data_style={}, init_style={}, best_style={}, **kwargs):
        # Dropdown menu of all subclasses of Model, incl. user-defined.
        self.models_menu = Dropdown()
        if all_models is None:
            all_models = {m.__name__: m for m in Model.__subclasses__()}
        self.models_menu.values = all_models
        self.models_menu.on_trait_change(self._on_model_value_change,
                                             'value')
        # Button to trigger fitting.
        self.fit_button = Button(description='Fit')
        self.fit_button.on_click(self._on_fit_button_click)

        # Button to trigger guessing.
        self.guess_button = Button(description='Auto-Guess')
        self.guess_button.on_click(self._on_guess_button_click)

        # Parameter widgets are not built here. They are (re-)built when
        # the model is (re-)set.
        super(NotebookFitter, self).__init__(data, model, axes_style,
                                             data_style, init_style,
                                             best_style, **kwargs)

    def _repr_html_(self):
        display(self.models_menu)
        button_box = Box()
        button_box.children = [self.fit_button, self.guess_button]
        display(button_box)
        button_box.add_class('hbox')
        for pw in self.param_widgets:
            display(pw)
        self.plot()

    def guess(self):
        guessing_successful = super(NotebookFitter, self).guess()
        self.guess_button.disabled = not guessing_successful

    def _finalize_model(self, value):
        first_run = not hasattr(self, 'param_widgets')
        if not first_run:
            # Remove all Parameter widgets, and replace them with widgets
            # for the new model.
            for pw in self.param_widgets:
                pw.close()
        self.models_menu.value = value
        self.param_widgets = [ParameterWidgetGroup(p)
                              for _, p in self._current_params.items()]
        if not first_run:
            for pw in self.param_widgets:
                display(pw)

    def _finalize_params(self):
        for pw in self.param_widgets:
            pw.value = self._current_params[pw.name].value
            pw.min = self._current_params[pw.name].min
            pw.max = self._current_params[pw.name].max
            pw.vary = self._current_params[pw.name].vary

    def plot(self):
        clear_output(wait=True)
        super(NotebookFitter, self).plot()

    def fit(self):
        super(NotebookFitter, self).fit()
        self.plot()


def _normalize_kwargs(kwargs, kind='patch'):
    """Convert matplotlib keywords from short to long form."""
    # Source:
    # github.com/tritemio/FRETBursts/blob/fit_experim/fretbursts/burst_plot.py
    if kind == 'line2d':
        long_names = dict(c='color', ls='linestyle', lw='linewidth',
                          mec='markeredgecolor', mew='markeredgewidth',
                          mfc='markerfacecolor', ms='markersize',)
    elif kind == 'patch':
        long_names = dict(c='color', ls='linestyle', lw='linewidth',
                          ec='edgecolor', fc='facecolor',)
    for short_name in long_names:
        if short_name in kwargs:
            kwargs[long_names[short_name]] = kwargs.pop(short_name)
    return kwargs


if has_ipython:
    Fitter = NotebookFitter
elif has_matplotlib:
    Fitter = MPLFitter
else:
    # no dependencies beyond core lmfit dependencies
    Fitter = BaseFitter