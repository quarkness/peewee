from bisect import bisect_left
from bisect import bisect_right
from collections import defaultdict
from collections import deque
from collections import namedtuple
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
from inspect import isclass
import calendar
import datetime
import decimal
import hashlib
import itertools
import logging
import operator
import re
import socket
import struct
import sys
import threading
import time
import uuid
import warnings

try:
    from pysqlite2 import dbapi2 as pysq3
except ImportError:
    pysq3 = None
try:
    import sqlite3
except ImportError:
    sqlite3 = pysq3
else:
    if pysq3 and pysq3.sqlite_version_info >= sqlite3.sqlite_version_info:
        sqlite3 = pysq3
try:
    from psycopg2cffi import compat
    compat.register()
except ImportError:
    pass
try:
    import psycopg2
    from psycopg2 import extensions as pg_extensions
except ImportError:
    psycopg2 = None
try:
    import MySQLdb as mysql  # prefer the C module.
except ImportError:
    try:
        import pymysql as mysql
    except ImportError:
        mysql = None


__version__ = '3.0.0'
__all__ = [
    'AutoField',
    'BareField',
    'BigIntegerField',
    'BlobField',
    'BooleanField',
    'Case',
    'Cast',
    'CharField',
    'Check',
    'Column',
    'CompositeKey',
    'Context',
    'Database',
    'DatabaseError',
    'DataError',
    'DateField',
    'DateTimeField',
    'DecimalField',
    'DeferredForeignKey',
    'DeferredThroughModel',
    'DJANGO_MAP',
    'DoesNotExist',
    'DoubleField',
    'DQ',
    'Field',
    'FixedCharField',
    'FloatField',
    'fn',
    'ForeignKeyField',
    'ImproperlyConfigured',
    'Index',
    'IntegerField',
    'IntegrityError',
    'InterfaceError',
    'InternalError',
    'IPField',
    'JOIN',
    'ManyToManyField',
    'Model',
    'ModelIndex',
    'MySQLDatabase',
    'NotSupportedError',
    'OP',
    'OperationalError',
    'PostgresqlDatabase',
    'prefetch',
    'ProgrammingError',
    'Proxy',
    'SchemaManager',
    'SmallIntegerField',
    'SQL',
    'SqliteDatabase',
    'Table',
    'TextField',
    'TimeField',
    'TimestampField',
    'Tuple',
    'UUIDField',
    'Value',
    'Window',
]

try:  # Python 2.7+
    from logging import NullHandler
except ImportError:
    class NullHandler(logging.Handler):
        def emit(self, record):
            pass

logger = logging.getLogger('peewee')
logger.addHandler(NullHandler())

# Import any speedups or provide alternate implementations.
try:
    from playhouse._speedups import quote
except ImportError:
    def quote(path, quote_char):
        quotes = (quote_char, quote_char)
        if len(path) == 1:
            return path[0].join(quotes)
        return '.'.join([part.join(quotes) for part in path])


PY26 = sys.version_info[:2] == (2, 6)
if sys.version_info[0] == 2:
    text_type = unicode
    bytes_type = str
    exec('def reraise(tp, value, tb=None): raise tp, value, tb')
    def print_(s):
        sys.stdout.write(s)
        sys.stdout.write('\n')
else:
    import builtins
    from collections import Callable
    from functools import reduce
    callable = lambda c: isinstance(c, Callable)
    text_type = str
    bytes_type = bytes
    basestring = str
    long = int
    print_ = getattr(builtins, 'print')
    def reraise(tp, value, tb=None):
        if value.__traceback__ is not tb:
            raise value.with_traceback(tb)
        raise value


if sqlite3:
    sqlite3.register_adapter(decimal.Decimal, str)
    sqlite3.register_adapter(datetime.date, str)
    sqlite3.register_adapter(datetime.time, str)


__date_parts__ = set(('year', 'month', 'day', 'hour', 'minute', 'second'))

# Sqlite does not support the `date_part` SQL function, so we will define an
# implementation in python.
__sqlite_datetime_formats__ = (
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M:%S.%f',
    '%Y-%m-%d',
    '%H:%M:%S',
    '%H:%M:%S.%f',
    '%H:%M')

__sqlite_date_trunc__ = {
    'year': '%Y',
    'month': '%Y-%m',
    'day': '%Y-%m-%d',
    'hour': '%Y-%m-%d %H',
    'minute': '%Y-%m-%d %H:%M',
    'second': '%Y-%m-%d %H:%M:%S'}

__mysql_date_trunc__ = __sqlite_date_trunc__.copy()
__mysql_date_trunc__['minute'] = '%Y-%m-%d %H:%i'
__mysql_date_trunc__['second'] = '%Y-%m-%d %H:%i:%S'

def _sqlite_date_part(lookup_type, datetime_string):
    assert lookup_type in __date_parts__
    if not datetime_string:
        return
    dt = format_date_time(datetime_string, __sqlite_datetime_formats__)
    return getattr(dt, lookup_type)

def _sqlite_date_trunc(lookup_type, datetime_string):
    assert lookup_type in __sqlite_date_trunc__
    if not datetime_string:
        return
    dt = format_date_time(datetime_string, __sqlite_datetime_formats__)
    return dt.strftime(__sqlite_date_trunc__[lookup_type])


def __deprecated__(s):
    warnings.warn(s, DeprecationWarning)


class attrdict(dict):
    def __getattr__(self, attr):
        try:
            return self[attr]
        except KeyError:
            raise AttributeError(attr)
    def __setattr__(self, attr, value): self[attr] = value
    def __iadd__(self, rhs): self.update(rhs); return self
    def __add__(self, rhs): d = attrdict(self); d.update(rhs); return d

SENTINEL = object()

#: Operations for use in SQL expressions.
OP = attrdict(
    AND='AND',
    OR='OR',
    ADD='+',
    SUB='-',
    MUL='*',
    DIV='/',
    BIN_AND='&',
    BIN_OR='|',
    XOR='#',
    MOD='%',
    EQ='=',
    LT='<',
    LTE='<=',
    GT='>',
    GTE='>=',
    NE='!=',
    IN='IN',
    NOT_IN='NOT IN',
    IS='IS',
    IS_NOT='IS NOT',
    LIKE='LIKE',
    ILIKE='ILIKE',
    BETWEEN='BETWEEN',
    REGEXP='REGEXP',
    CONCAT='||')

# To support "django-style" double-underscore filters, create a mapping between
# operation name and operation code, e.g. "__eq" == OP.EQ.
DJANGO_MAP = attrdict({
    'eq': OP.EQ,
    'lt': OP.LT,
    'lte': OP.LTE,
    'gt': OP.GT,
    'gte': OP.GTE,
    'ne': OP.NE,
    'in': OP.IN,
    'is': OP.IS,
    'like': OP.LIKE,
    'ilike': OP.ILIKE,
    'regexp': OP.REGEXP})

#: Mapping of field type to the data-type supported by the database. Databases
#: may override or add to this list.
FIELD = attrdict(
    AUTO='INTEGER',
    BIGINT='BIGINT',
    BLOB='BLOB',
    BOOL='SMALLINT',
    CHAR='CHAR',
    DATE='DATE',
    DATETIME='DATETIME',
    DECIMAL='DECIMAL',
    DEFAULT='',
    DOUBLE='REAL',
    FLOAT='REAL',
    INT='INTEGER',
    SMALLINT='SMALLINT',
    TEXT='TEXT',
    TIME='TIME',
    UUID='TEXT',
    VARCHAR='VARCHAR')

#: Join helpers (for convenience) -- all join types are supported, this object
#: is just to help avoid introducing errors by using strings everywhere.
JOIN = attrdict(
    INNER='INNER',
    LEFT_OUTER='LEFT OUTER',
    RIGHT_OUTER='RIGHT OUTER',
    FULL='FULL',
    FULL_OUTER='FULL OUTER',
    CROSS='CROSS')

# Row representations.
ROW = attrdict(
    TUPLE=1,
    DICT=2,
    NAMED_TUPLE=3,
    CONSTRUCTOR=4,
    MODEL=5)

SCOPE_NORMAL = 1
SCOPE_SOURCE = 2
SCOPE_VALUES = 4
SCOPE_CTE = 8
SCOPE_COLUMN = 16


# Helper functions that are used in various parts of the codebase.
MODEL_BASE = '_metaclass_helper_'

def with_metaclass(meta, base=object):
    return meta(MODEL_BASE, (base,), {})

def merge_dict(source, overrides):
    merged = source.copy()
    if overrides:
        merged.update(overrides)
    return merged

if sys.version_info[:2] == (2, 6):
    import types
    def is_model(obj):
        if isinstance(obj, (type, types.ClassType)):
            return issubclass(obj, Model)
        return False
else:
    def is_model(obj):
        if isclass(obj):
            return issubclass(obj, Model)
        return False

def ensure_tuple(value):
    if value is not None:
        return value if isinstance(value, (list, tuple)) else (value,)

def ensure_entity(value):
    if value is not None:
        return value if isinstance(value, Node) else Entity(value)


class _callable_context_manager(object):
    def __call__(self, fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            with self:
                return fn(*args, **kwargs)
        return inner


class Proxy(object):
    """
    Create a proxy or placeholder for another object.
    """
    __slots__ = ('obj', '_callbacks')

    def __init__(self):
        self._callbacks = []
        self.initialize(None)

    def initialize(self, obj):
        """
        :param obj: Object to proxy to.

        Bind the proxy to the given object. Afterwards all attribute lookups
        and method calls on the proxy will be sent to the given object.

        Any callbacks that have been registered will be called.
        """
        self.obj = obj
        for callback in self._callbacks:
            callback(obj)

    def attach_callback(self, callback):
        """
        :param callback: A function that accepts a single parameter, the bound
            object.
        :returns: self

        Add a callback to be executed when the proxy is initialized.
        """
        self._callbacks.append(callback)
        return callback

    def __getattr__(self, attr):
        if self.obj is None:
            raise AttributeError('Cannot use uninitialized Proxy.')
        return getattr(self.obj, attr)

    def __setattr__(self, attr, value):
        if attr not in self.__slots__:
            raise AttributeError('Cannot set attribute on proxy.')
        return super(Proxy, self).__setattr__(attr, value)


# SQL Generation.


class AliasManager(object):
    """
    Manages the aliases assigned to :py:class:`Source` objects in SELECT
    queries, so as to avoid ambiguous references when multiple sources are
    used in a single query.
    """
    def __init__(self):
        # A list of dictionaries containing mappings at various depths.
        self._counter = 0
        self._current_index = 0
        self._mapping = []
        self.push()

    @property
    def mapping(self):
        return self._mapping[self._current_index - 1]

    def add(self, source):
        """
        Add a source to the AliasManager's internal registry at the current
        scope. The alias will be automatically generated using the following
        scheme (where each level of indentation refers to a new scope):

        :param Source source: Make the manager aware of a new source. If the
            source has already been added, the call is a no-op.
        """
        if source not in self.mapping:
            self._counter += 1
            self[source] = 't%d' % self._counter
        return self.mapping[source]

    def get(self, source, any_depth=False):
        """
        Return the alias for the source in the current scope. If the source
        does not have an alias, it will be given the next available alias.

        :param Source source: The source whose alias should be retrieved.
        :returns: The alias already assigned to the source, or the next
            available alias.
        :rtype: str
        """
        if any_depth:
            for idx in reversed(range(self._current_index)):
                if source in self._mapping[idx]:
                    return self._mapping[idx][source]
        return self.add(source)

    def __getitem__(self, source):
        return self.get(source)

    def __setitem__(self, source, alias):
        """
        Manually set the alias for the source at the current scope.

        :param Source source: The source for which we set the alias.
        """
        self.mapping[source] = alias

    def push(self):
        """
        Push a new scope onto the stack.
        """
        self._current_index += 1
        if self._current_index > len(self._mapping):
            self._mapping.append({})

    def pop(self):
        """
        Pop scope from the stack.
        """
        if self._current_index == 1:
            raise ValueError('Cannot pop() from empty alias manager.')
        self._current_index -= 1


class State(namedtuple('_State', ('scope', 'parentheses', 'subquery',
                                  'settings'))):
    """
    Lightweight object for representing the state at a given scope. During SQL
    generation, each object visited by the :py:class:`Context` can inspect the
    state. The :py:class:`State` class allows Peewee to do things like:

    * Use a common interface for field types or SQL expressions, but use
      vendor-specific data-types or operators.
    * Compile a :py:class:`Column` instance into a fully-qualified attribute,
      as a named alias, etc, depending on the value of the ``scope``.
    * Ensure parentheses are used appropriately.

    :param int scope: The scope rules to be applied while the state is active.
    :param bool parentheses: Wrap the contained SQL in parentheses.
    :param bool subquery: Whether the current state is a child of an outer
        query.
    :param dict kwargs: Arbitrary settings which should be applied in the
        current state.
    """
    def __new__(cls, scope=SCOPE_NORMAL, parentheses=False, subquery=False,
                **kwargs):
        return super(State, cls).__new__(cls, scope, parentheses, subquery,
                                         kwargs)

    def __call__(self, scope=None, parentheses=None, subquery=None, **kwargs):
        # All state is "inherited" except parentheses.
        scope = self.scope if scope is None else scope
        subquery = self.subquery if subquery is None else subquery
        settings = self.settings
        if kwargs:
            settings.update(kwargs)
        return State(scope, parentheses, subquery, **settings)

    def __getattr__(self, attr_name):
        return self.settings.get(attr_name)


def __scope_context__(scope):
    @contextmanager
    def inner(self, **kwargs):
        with self(scope=scope, **kwargs):
            yield self
    return inner


class Context(object):
    """
    Converts Peewee structures into parameterized SQL queries.

    Peewee structures should all implement a `__sql__` method, which will be
    called by the `Context` class during SQL generation. The `__sql__` method
    accepts a single parameter, the `Context` instance, which allows for
    recursive descent and introspection of scope and state.
    """
    def __init__(self, **settings):
        self.stack = []
        self._sql = []
        self._values = []
        self.alias_manager = AliasManager()
        self.state = State(**settings)

    def column_sort_key(self, item):
        return item[0].get_sort_key(self)

    @property
    def scope(self):
        """
        Return the currently-active scope rules.
        """
        return self.state.scope

    @property
    def parentheses(self):
        """
        Return whether the current state is wrapped in parentheses.
        """
        return self.state.parentheses

    @property
    def subquery(self):
        """
        Return whether the current state is the child of another query.
        """
        return self.state.subquery

    def __call__(self, **overrides):
        if overrides and overrides.get('scope') == self.scope:
            del overrides['scope']

        self.stack.append(self.state)
        self.state = self.state(**overrides)
        return self

    #: The default scope. Sources are referred to by alias, columns by
    #: dotted-path from the source.
    scope_normal = __scope_context__(SCOPE_NORMAL)

    #: Scope used when defining sources, e.g. in the column list and FROM
    #: clause of a SELECT query. This scope is used for defining the
    #: fully-qualified name of the source and assigning an alias.
    scope_source = __scope_context__(SCOPE_SOURCE)

    #: Scope used for UPDATE, INSERT or DELETE queries, where instead of
    #: referencing a source by an alias, we refer to it directly. Similarly,
    #: since there is a single table, columns do not need to be referenced
    #: by dotted-path.
    scope_values = __scope_context__(SCOPE_VALUES)

    #: Scope used when generating the contents of a common-table-expression.
    #: Used after a WITH statement, when generating the definition for a CTE
    #: (as opposed to merely a reference to one).
    scope_cte = __scope_context__(SCOPE_CTE)

    #: Scope used when generating SQL for a column. Ensures that the column is
    #: rendered with it's correct alias. Was needed because when referencing
    #: the inner projection of a sub-select, Peewee would render the full
    #: SELECT query as the "source" of the column (instead of the query's alias
    #: + . + column).  This scope allows us to avoid rendering the full query
    #: when we only need the alias.
    scope_column = __scope_context__(SCOPE_COLUMN)

    def __enter__(self):
        if self.parentheses:
            self.literal('(')
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.parentheses:
            self.literal(')')
        self.state = self.stack.pop()

    @contextmanager
    def push_alias(self):
        self.alias_manager.push()
        yield
        self.alias_manager.pop()

    def sql(self, obj):
        """
        Append a composable Node object, sub-context, or other object to the
        query AST. Python values, such as integers, strings, floats, etc. are
        treated as parameterized values.

        :returns: The updated Context object.
        """
        if isinstance(obj, (Node, Context)):
            return obj.__sql__(self)
        elif is_model(obj):
            return obj._meta.table.__sql__(self)
        else:
            return self.sql(Value(obj))

    def literal(self, keyword):
        """
        Append a string-literal to the current query AST.

        :returns: The updated Context object.
        """
        self._sql.append(keyword)
        return self

    def value(self, value, converter=None):
        if converter is None:
            converter = self.state.converter
        if converter is not None:
            value = converter(value)
        self._values.append(value)
        return self

    def __sql__(self, ctx):
        ctx._sql.extend(self._sql)
        ctx._values.extend(self._values)
        return ctx

    def parse(self, node):
        """
        :param Node node: Instance of a Node subclass.
        :returns: a 2-tuple consisting of (sql, parameters).

        Convert the given node to a SQL AST and return a 2-tuple consisting
        of the SQL query and the parameters.
        """
        return self.sql(node).query()

    def query(self):
        """
        :returns: a 2-tuple consisting of (sql, parameters) for the context.
        """
        return ''.join(self._sql), self._values


# AST.


class Node(object):
    """
    Base-class for all components which make up the AST for a SQL query.
    """
    def clone(self):
        obj = self.__class__.__new__(self.__class__)
        obj.__dict__ = self.__dict__.copy()
        return obj

    def __sql__(self, ctx):
        raise NotImplementedError

    @staticmethod
    def copy(method):
        """
        Decorator to use with Node methods that mutate the node's state.
        This allows method-chaining, e.g.:

            query = MyModel.select()
            new_query = query.where(MyModel.field == 'value')
        """
        def inner(self, *args, **kwargs):
            clone = self.clone()
            method(clone, *args, **kwargs)
            return clone
        return inner

    def unwrap(self):
        """
        API for recursively unwrapping "wrapped" nodes. Base case is to
        return self.
        """
        return self


class ColumnFactory(object):
    __slots__ = ('node',)

    def __init__(self, node):
        self.node = node

    def __getattr__(self, attr):
        return Column(self.node, attr)


class _DynamicColumn(object):
    __slots__ = ()

    def __get__(self, instance, instance_type=None):
        if instance is not None:
            return ColumnFactory(instance)  # Implements __getattr__().
        return self


class _ExplicitColumn(object):
    __slots__ = ()

    def __get__(self, instance, instance_type=None):
        if instance is not None:
            raise AttributeError(
                '%s specifies columns explicitly, and does not support '
                'dynamic column lookups.' % instance)
        return self


class Source(Node):
    """
    A source of row tuples, for example a table, join, or select query. By
    default provides a "magic" attribute named "c" that is a factory for
    column/attribute lookups, for example::

        User = Table('users')
        query = (User
                 .select(User.c.username)
                 .where(User.c.active == True)
                 .order_by(User.c.username))
    """
    c = _DynamicColumn()

    def __init__(self, alias=None):
        super(Source, self).__init__()
        self._alias = alias

    @Node.copy
    def alias(self, name):
        """
        Returns a copy of the object with the given alias applied.
        """
        self._alias = name

    def select(self, *columns):
        """
        :param columns: :py:class:`Column` instances, expressions, functions,
            sub-queries, or anything else that you would like to select.

        Create a :py:class:`Select` query on the table. If the table explicitly
        declares columns and no columns are provided, then by default all the
        table's defined columns will be selected.
        """
        return Select((self,), columns)

    def join(self, dest, join_type='INNER', on=None):
        """
        :param Source dest: Join the table with the given destination.
        :param str join_type: Join type.
        :param on: Expression to use as join predicate.
        :returns: a :py:class:`Join` instance.
        """
        return Join(self, dest, join_type, on)

    def left_outer_join(self, dest, on=None):
        """
        :param Source dest: Join the table with the given destination.
        :param on: Expression to use as join predicate.
        :returns: a :py:class:`Join` instance.

        Convenience method for calling :py:meth:`~Source.join` using a LEFT
        OUTER join.
        """
        return Join(self, dest, JOIN.LEFT_OUTER, on)

    def get_sort_key(self, ctx):
        if self._alias:
            return (self._alias,)
        return (ctx.alias_manager[self],)

    def apply_alias(self, ctx):
        # If we are defining the source, include the "AS alias" declaration. An
        # alias is created for the source if one is not already defined.
        if ctx.scope == SCOPE_SOURCE:
            if self._alias:
                ctx.alias_manager[self] = self._alias
            ctx.literal(' AS ').sql(Entity(ctx.alias_manager[self]))
        return ctx

    def apply_column(self, ctx):
        if self._alias:
            ctx.alias_manager[self] = self._alias
        return ctx.sql(Entity(ctx.alias_manager[self]))


class _HashableSource(object):
    def __init__(self, *args, **kwargs):
        super(_HashableSource, self).__init__(*args, **kwargs)
        self._update_hash()

    @Node.copy
    def alias(self, name):
        self._alias = name
        self._update_hash()

    def _update_hash(self):
        self._hash = self._get_hash()

    def _get_hash(self):
        return hash((self.__class__, self._path, self._alias))

    def __hash__(self):
        return self._hash

    def __eq__(self, other):
        return self._hash == other._hash

    def __ne__(self, other):
        return not (self == other)


def __bind_database__(meth):
    @wraps(meth)
    def inner(self, *args, **kwargs):
        result = meth(self, *args, **kwargs)
        if self._database:
            return result.bind(self._database)
        return result
    return inner


def __join__(join_type='INNER', inverted=False):
    def method(self, other):
        if inverted:
            self, other = other, self
        return Join(self, other, join_type=join_type)
    return method


class BaseTable(Source):
    """
    Base class for table-like objects, which support JOINs via operator
    overloading.
    """
    __and__ = __join__(JOIN.INNER)
    __add__ = __join__(JOIN.LEFT_OUTER)
    __sub__ = __join__(JOIN.RIGHT_OUTER)
    __or__ = __join__(JOIN.FULL_OUTER)
    __mul__ = __join__(JOIN.CROSS)
    __rand__ = __join__(JOIN.INNER, inverted=True)
    __radd__ = __join__(JOIN.LEFT_OUTER, inverted=True)
    __rsub__ = __join__(JOIN.RIGHT_OUTER, inverted=True)
    __ror__ = __join__(JOIN.FULL_OUTER, inverted=True)
    __rmul__ = __join__(JOIN.CROSS, inverted=True)


class _BoundTableContext(_callable_context_manager):
    def __init__(self, table, database):
        self.table = table
        self.database = database

    def __enter__(self):
        self._orig_database = self.table._database
        self.table.bind(self.database)
        if self.table._model is not None:
            self.table._model.bind(self.database)
        return self.table

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.table.bind(self._orig_database)
        if self.table._model is not None:
            self.table._model.bind(self._orig_database)


class Table(_HashableSource, BaseTable):
    """
    Represents a table in the database (or a table-like object such as a view).

    :param str name: Database table name
    :param tuple columns: List of column names (optional).
    :param str primary_key: Name of primary key column.
    :param str schema: Schema name used to access table (if necessary).
    :param str alias: Alias to use for table in SQL queries.

    .. note::
        If columns are specified, the magic "c" attribute will be disabled.

    When columns are not explicitly defined, tables have a special attribute
    "c" which is a factory that provides access to table columns dynamically.

    Example::

        User = Table('users')
        query = (User
                 .select(User.c.id, User.c.username)
                 .order_by(User.c.username))

    Equivalent example when columns **are** specified::

        User = Table('users', ('id', 'username'))
        query = (User
                 .select(User.id, User.username)
                 .order_by(User.username))
    """
    def __init__(self, name, columns=None, primary_key=None, schema=None,
                 alias=None, _model=None, _database=None):
        self.__name__ = name
        self._columns = columns
        self._primary_key = primary_key
        self._schema = schema
        self._path = (schema, name) if schema else (name,)
        self._model = _model
        self._database = _database
        super(Table, self).__init__(alias=alias)

        # Allow tables to restrict what columns are available.
        if columns is not None:
            self.c = _ExplicitColumn()
            for column in columns:
                setattr(self, column, Column(self, column))

        if primary_key:
            col_src = self if self._columns else self.c
            self.primary_key = getattr(col_src, primary_key)
        else:
            self.primary_key = None

    def clone(self):
        # Ensure a deep copy of the column instances.
        return Table(
            self.__name__,
            columns=self._columns,
            primary_key=self._primary_key,
            schema=self._schema,
            alias=self._alias,
            _model=self._model,
            _database=self._database)

    def bind(self, database=None):
        """
        :param database: :py:class:`Database` object.

        Bind this table to the given database (or unbind by leaving empty).

        When a table is *bound* to a database, queries may be executed against
        it without the need to specify the database in the query's execute
        method.
        """
        self._database = database
        return self

    def bind_ctx(self, database=None):
        """
        :param database: :py:class:`Database` object.

        Return a context manager that will bind the table to the given database
        for the duration of the wrapped block.
        """
        return _BoundTableContext(self, database)

    def _get_hash(self):
        return hash((self.__class__, self._path, self._alias, self._model))

    @__bind_database__
    def select(self, *columns):
        """
        :param columns: :py:class:`Column` instances, expressions, functions,
            sub-queries, or anything else that you would like to select.

        Create a :py:class:`Select` query on the table. If the table explicitly
        declares columns and no columns are provided, then by default all the
        table's defined columns will be selected.

        Example::

            User = Table('users', ('id', 'username'))

            # Because columns were defined on the Table, we will default to
            # selecting both of the User table's columns.
            # Evaluates to SELECT id, username FROM users
            query = User.select()

            Note = Table('notes')
            query = (Note
                     .select(Note.c.content, Note.c.timestamp, User.username)
                     .join(User, on=(Note.c.user_id == User.id))
                     .where(Note.c.is_published == True)
                     .order_by(Note.c.timestamp.desc()))

            # Using a function to select users and the number of notes they
            # have authored.
            query = (User
                     .select(
                        User.username,
                        fn.COUNT(Note.c.id).alias('n_notes'))
                     .join(
                        Note,
                        JOIN.LEFT_OUTER,
                        on=(User.id == Note.c.user_id))
                     .order_by(fn.COUNT(Note.c.id).desc()))
        """
        if not columns and self._columns:
            columns = [Column(self, column) for column in self._columns]
        return Select((self,), columns)

    @__bind_database__
    def insert(self, insert=None, columns=None, **kwargs):
        """
        :param insert: A dictionary mapping column to value, an iterable that
            yields dictionaries (i.e. list), or a :py:class:`Select` query.
        :param list columns: The list of columns to insert into when the
            data being inserted is not a dictionary.
        :param kwargs: Mapping of column-name to value.

        Create a :py:class:`Insert` query into the table.
        """
        if kwargs:
            insert = {} if insert is None else insert
            src = self if self._columns else self.c
            for key, value in kwargs.items():
                insert[getattr(src, key)] = value
        return Insert(self, insert=insert, columns=columns)

    @__bind_database__
    def replace(self, insert=None, columns=None, **kwargs):
        """
        :param insert: A dictionary mapping column to value, an iterable that
            yields dictionaries (i.e. list), or a :py:class:`Select` query.
        :param list columns: The list of columns to insert into when the
            data being inserted is not a dictionary.
        :param kwargs: Mapping of column-name to value.

        Create a :py:class:`Insert` query into the table whose conflict
        resolution method is to replace.
        """
        return (self
                .insert(insert=insert, columns=columns)
                .on_conflict('REPLACE'))

    @__bind_database__
    def update(self, update=None, **kwargs):
        """
        :param update: A dictionary mapping column to value.
        :param kwargs: Mapping of column-name to value.

        Create a :py:class:`Update` query for the table.
        """
        if kwargs:
            update = {} if update is None else update
            for key, value in kwargs.items():
                src = self if self._columns else self.c
                update[getattr(self, key)] = value
        return Update(self, update=update)

    @__bind_database__
    def delete(self):
        """
        Create a :py:class:`Delete` query for the table.
        """
        return Delete(self)

    def __sql__(self, ctx):
        if ctx.scope == SCOPE_VALUES:
            # Return the quoted table name.
            return ctx.sql(Entity(*self._path))

        if self._alias:
            ctx.alias_manager[self] = self._alias

        if ctx.scope == SCOPE_SOURCE:
            # Define the table and its alias.
            return self.apply_alias(ctx.sql(Entity(*self._path)))
        else:
            # Refer to the table using the alias.
            return self.apply_column(ctx)


class Join(BaseTable):
    """
    Represent a JOIN between to table-like objects.

    :param lhs: Left-hand side of the join.
    :param rhs: Right-hand side of the join.
    :param join_type: Type of join. e.g. JOIN.INNER, JOIN.LEFT_OUTER, etc.
    :param on: Expression describing the join predicate.
    :param str alias: Alias to apply to joined data.
    """
    def __init__(self, lhs, rhs, join_type=JOIN.INNER, on=None, alias=None):
        super(Join, self).__init__(alias=alias)
        self.lhs = lhs
        self.rhs = rhs
        self.join_type = join_type
        self._on = on

    def on(self, predicate):
        """
        :param Expression predicate: join predicate.

        Specify the predicate expression used for this join.
        """
        self._on = predicate
        return self

    def __sql__(self, ctx):
        (ctx
         .sql(self.lhs)
         .literal(' %s JOIN ' % self.join_type)
         .sql(self.rhs))
        if self._on is not None:
            ctx.literal(' ON ').sql(self._on)
        return ctx


class CTE(_HashableSource, Source):
    """
    Represent a common-table-expression.

    :param name: Name for the CTE.
    :param query: :py:class:`Select` query describing CTE.
    :param bool recursive: Whether the CTE is recursive.
    :param list columns: Explicit list of columns produced by CTE (optional).
    """
    def __init__(self, name, query, recursive=False, columns=None):
        self._alias = name
        self._nested_cte_list = query._cte_list
        query._cte_list = ()
        self._query = query
        self._recursive = recursive
        self._columns = columns
        super(CTE, self).__init__(alias=name)

    def _get_hash(self):
        return hash((self.__class__, self._alias, id(self._query)))

    def __sql__(self, ctx):
        if ctx.scope != SCOPE_CTE:
            return ctx.sql(Entity(self._alias))

        with ctx.push_alias():
            ctx.alias_manager[self] = self._alias
            ctx.sql(Entity(self._alias))

            if self._columns:
                ctx.literal(' ').sql(EnclosedNodeList(self._columns))
            ctx.literal(' AS (')
            with ctx.scope_normal():
                ctx.sql(self._query)
            ctx.literal(')')
        return ctx


class ColumnBase(Node):
    """
    Base-class for column-like objects, attributes or expressions.

    Column-like objects can be composed using various operators and special
    methods.

    * ``&``: Logical AND
    * ``|``: Logical OR
    * ``+``: Addition
    * ``-``: Subtraction
    * ``*``: Multiplication
    * ``/``: Division
    * ``^``: Exclusive-OR
    * ``==``: Equality
    * ``!=``: Inequality
    * ``>``: Greater-than
    * ``<``: Less-than
    * ``>=``: Greater-than or equal
    * ``<=``: Less-than or equal
    * ``<<``: ``IN``
    * ``>>``: ``IS`` (i.e. ``IS NULL``)
    * ``%``: ``LIKE``
    * ``**``: ``ILIKE``
    * ``bin_and()``: Binary AND
    * ``bin_or()``: Binary OR
    * ``in_()``: ``IN``
    * ``not_in()``: ``NOT IN``
    * ``regexp()``: ``REGEXP``
    * ``is_null(True/False)``: ``IS NULL`` or ``IS NOT NULL``
    * ``contains(s)``: ``LIKE %s%``
    * ``startswith(s)``: ``LIKE s%``
    * ``endswith(s)``: ``LIKE %s``
    * ``between(low, high)``: ``BETWEEN low AND high``
    * ``concat()``: ``||``
    """
    def alias(self, alias):
        """
        :param str alias: Alias for the given column-like object.
        :returns: a :py:class:`Alias` object.

        Indicate the alias that should be given to the specified column-like
        object.
        """
        if alias:
            return Alias(self, alias)
        return self

    def unalias(self):
        return self

    def cast(self, as_type):
        """
        :param str as_type: Type name to cast to.
        :returns: a :py:class:`Cast` object.

        Create a ``CAST`` expression.
        """
        return Cast(self, as_type)

    def asc(self):
        """
        :returns: an ascending :py:class:`Ordering` object for the column.
        """
        return Asc(self)
    __pos__ = asc

    def desc(self):
        """
        :returns: a descending :py:class:`Ordering` object for the column.
        """
        return Desc(self)
    __neg__ = desc

    def __invert__(self):
        """
        :returns: a :py:class:`Negated` wrapper for the column.
        """
        return Negated(self)

    def _e(op, inv=False):
        """
        Lightweight factory which returns a method that builds an Expression
        consisting of the left-hand and right-hand operands, using `op`.
        """
        def inner(self, rhs):
            if inv:
                return Expression(rhs, op, self)
            return Expression(self, op, rhs)
        return inner
    __and__ = _e(OP.AND)
    __or__ = _e(OP.OR)

    __add__ = _e(OP.ADD)
    __sub__ = _e(OP.SUB)
    __mul__ = _e(OP.MUL)
    __div__ = __truediv__ = _e(OP.DIV)
    __xor__ = _e(OP.XOR)
    __radd__ = _e(OP.ADD, inv=True)
    __rsub__ = _e(OP.SUB, inv=True)
    __rmul__ = _e(OP.MUL, inv=True)
    __rdiv__ = __rtruediv__ = _e(OP.DIV, inv=True)
    __rand__ = _e(OP.AND, inv=True)
    __ror__ = _e(OP.OR, inv=True)
    __rxor__ = _e(OP.XOR, inv=True)

    def __eq__(self, rhs):
        op = OP.IS if rhs is None else OP.EQ
        return Expression(self, op, rhs)
    def __ne__(self, rhs):
        op = OP.IS_NOT if rhs is None else OP.NE
        return Expression(self, op, rhs)

    __lt__ = _e(OP.LT)
    __le__ = _e(OP.LTE)
    __gt__ = _e(OP.GT)
    __ge__ = _e(OP.GTE)
    __lshift__ = _e(OP.IN)
    __rshift__ = _e(OP.IS)
    __mod__ = _e(OP.LIKE)
    __pow__ = _e(OP.ILIKE)

    bin_and = _e(OP.BIN_AND)
    bin_or = _e(OP.BIN_OR)
    in_ = _e(OP.IN)
    not_in = _e(OP.NOT_IN)
    regexp = _e(OP.REGEXP)

    # Special expressions.
    def is_null(self, is_null=True):
        op = OP.IS if is_null else OP.IS_NOT
        return Expression(self, op, None)
    def contains(self, rhs):
        return Expression(self, OP.ILIKE, '%%%s%%' % rhs)
    def startswith(self, rhs):
        return Expression(self, OP.ILIKE, '%s%%' % rhs)
    def endswith(self, rhs):
        return Expression(self, OP.ILIKE, '%%%s' % rhs)
    def between(self, lo, hi):
        return Expression(self, OP.BETWEEN, NodeList((lo, SQL('AND'), hi)))
    def concat(self, rhs):
        return StringExpression(self, OP.CONCAT, rhs)
    def __getitem__(self, item):
        if isinstance(item, slice):
            if item.start is None or item.stop is None:
                raise ValueError('BETWEEN range must have both a start- and '
                                 'end-point.')
            return self.between(item.start, item.stop)
        return self == item

    def get_sort_key(self, ctx):
        return ()


class Column(ColumnBase):
    """
    :param Source source: Source for column.
    :param str name: Column name.

    Column on a table or a column returned by a sub-query.
    """
    def __init__(self, source, name):
        self.source = source
        self.name = name

    def get_sort_key(self, ctx):
        if ctx.scope == SCOPE_VALUES:
            return (self.name,)
        else:
            return self.source.get_sort_key(ctx) + (self.name,)

    def __hash__(self):
        return hash((self.source, self.name))

    def __sql__(self, ctx):
        if ctx.scope == SCOPE_VALUES:
            return ctx.sql(Entity(self.name))
        else:
            with ctx.scope_column():
                return ctx.sql(self.source).literal('.').sql(Entity(self.name))


class WrappedNode(ColumnBase):
    def __init__(self, node):
        self.node = node

    def unwrap(self):
        return self.node.unwrap()


class EntityFactory(object):
    __slots__ = ('node',)
    def __init__(self, node):
        self.node = node
    def __getattr__(self, attr):
        return Entity(self.node, attr)


class _DynamicEntity(object):
    __slots__ = ()
    def __get__(self, instance, instance_type=None):
        if instance is not None:
            return EntityFactory(instance._alias)  # Implements __getattr__().
        return self


class Alias(WrappedNode):
    """
    :param Node node: a column-like object.
    :param str alias: alias to assign to column.

    Create a named alias for the given column-like object.
    """
    c = _DynamicEntity()

    def __init__(self, node, alias):
        super(Alias, self).__init__(node)
        self._alias = alias

    def alias(self, alias=None):
        """
        :param str alias: new name (or None) for aliased column.

        Create a new :py:class:`Alias` for the aliased column-like object. If
        the new alias is ``None``, then the original column-like object is
        returned.
        """
        if alias is None:
            return self.node
        else:
            return Alias(self.node, alias)

    def unalias(self):
        return self.node

    def __sql__(self, ctx):
        if ctx.scope == SCOPE_SOURCE:
            return (ctx
                    .sql(self.node)
                    .literal(' AS ')
                    .sql(Entity(self._alias)))
        else:
            return ctx.sql(Entity(self._alias))


class Negated(WrappedNode):
    """
    Represents a negated column-like object.
    """
    def __invert__(self):
        return self.node

    def __sql__(self, ctx):
        return ctx.literal('NOT ').sql(self.node)


class Value(ColumnBase):
    """
    :param value: Python object or scalar value.
    :param converter: Function used to convert value into type the database
        understands.
    :param bool unpack: Whether lists or tuples should be unpacked into a list
        of values or treated as-is.

    Value to be used in a parameterized query. It is the responsibility of the
    caller to ensure that the value passed in can be adapted to a type the
    database driver understands.
    """
    def __init__(self, value, converter=None, unpack=True):
        self.value = value
        self.converter = converter
        self.multi = isinstance(self.value, (list, tuple)) and unpack
        if self.multi:
            self.values = []
            for item in self.value:
                if isinstance(item, Node):
                    self.values.append(item)
                else:
                    self.values.append(Value(item, self.converter))

    def __sql__(self, ctx):
        if self.multi:
            ctx.sql(EnclosedNodeList(self.values))
        else:
            (ctx
             .literal(ctx.state.param or '?')
             .value(self.value, self.converter))
        return ctx


def AsIs(value):
    """
    Represents a :py:class:`Value` that is treated as-is, and passed directly
    back to the database driver.
    """
    return Value(value, unpack=False)


class Cast(WrappedNode):
    """
    :param node: A column-like object.
    :param str cast: Type to cast to.

    Represents a ``CAST(<node> AS <cast>)`` expression.
    """
    def __init__(self, node, cast):
        super(Cast, self).__init__(node)
        self.cast = cast

    def __sql__(self, ctx):
        return (ctx
                .literal('CAST(')
                .sql(self.node)
                .literal(' AS %s)' % self.cast))


class Ordering(WrappedNode):
    """
    :param node: A column-like object.
    :param str direction: ASC or DESC
    :param str collation: Collation name to use for sorting.
    :param str nulls: Sort nulls (FIRST or LAST).

    Represent ordering by a column-like object.
    """
    def __init__(self, node, direction, collation=None, nulls=None):
        super(Ordering, self).__init__(node)
        self.direction = direction
        self.collation = collation
        self.nulls = nulls

    def collate(self, collation=None):
        """
        :param str collation: Collation name to use for sorting.
        """
        return Ordering(self.node, self.direction, collation)

    def __sql__(self, ctx):
        ctx.sql(self.node).literal(' %s' % self.direction)
        if self.collation:
            ctx.literal(' COLLATE %s' % self.collation)
        if self.nulls:
            ctx.literal(' NULLS %s' % self.nulls)
        return ctx


def Asc(node, collation=None, nulls=None):
    return Ordering(node, 'ASC', collation, nulls)


def Desc(node, collation=None, nulls=None):
    return Ordering(node, 'DESC', collation, nulls)


class Expression(ColumnBase):
    """
    :param lhs: Left-hand side.
    :param op: Operation.
    :param rhs: Right-hand side.
    :param bool flat: Whether to wrap expression in parentheses.

    Represent a binary expression of the form (lhs op rhs), e.g. (foo + 1).
    """
    def __init__(self, lhs, op, rhs, flat=False):
        self.lhs = lhs
        self.op = op
        self.rhs = rhs
        self.flat = flat

    def __sql__(self, ctx):
        overrides = {'parentheses': not self.flat}
        if isinstance(self.lhs, Field):
            overrides['converter'] = self.lhs.db_value
        else:
            overrides['converter'] = None

        if ctx.state.operations:
            op_sql = ctx.state.operations.get(self.op, self.op)
        else:
            op_sql = self.op

        with ctx(**overrides):
            if self.op == OP.IN and not Context().sql(self.rhs).query()[0]:
                return ctx.literal('0 = 1')

            return (ctx
                    .sql(self.lhs)
                    .literal(' %s ' % op_sql)
                    .sql(self.rhs))


class StringExpression(Expression):
    def __add__(self, rhs):
        return self.concat(rhs)
    def __radd__(self, lhs):
        return StringExpression(lhs, OP.CONCAT, self)


class Entity(ColumnBase):
    """
    :param path: Components that make up the dotted-path of the entity name.

    Represent a quoted entity in a query, such as a table, column, alias. The
    name may consist of multiple components, e.g. "a_table"."column_name".
    """
    def __init__(self, *path):
        self._path = [part.replace('"', '""') for part in path if part]

    def __getattr__(self, attr):
        """
        Factory method for creating sub-entities.
        """
        return Entity(*self._path + [attr])

    def get_sort_key(self, ctx):
        return tuple(self._path)

    def __hash__(self):
        return hash((self.__class__.__name__, tuple(self._path)))

    def __sql__(self, ctx):
        return ctx.literal(quote(self._path, ctx.state.quote or '"'))


class SQL(ColumnBase):
    """
    :param str sql: SQL query string.
    :param tuple params: Parameters for query (optional).

    Represent a parameterized SQL query or query-fragment.
    """
    def __init__(self, sql, params=None):
        self.sql = sql
        self.params = params

    def __sql__(self, ctx):
        ctx.literal(self.sql)
        if self.params:
            for param in self.params:
                if isinstance(param, Node):
                    ctx.sql(param)
                else:
                    ctx.value(param)
        return ctx


def Check(constraint):
    """
    :param str constraint: Constraint SQL.

    Represent a CHECK constraint.
    """
    return SQL('CHECK (%s)' % constraint)


class Function(ColumnBase):
    """
    :param str name: Function name.
    :param tuple arguments: Arguments to function.
    :param bool coerce: Whether to coerce the function result to a particular
        data-type when reading function return values from the cursor.

    Represent an arbitrary SQL function call.

    .. note::
        Rather than instantiating this class directly, it is recommended to use
        the ``fn`` helper.

    Example of using ``fn`` to call an arbitrary SQL function::

        # Query users and count of tweets authored.
        query = (User
                 .select(User.username, fn.COUNT(Tweet.id).alias('ct'))
                 .join(Tweet, JOIN.LEFT_OUTER, on=(User.id == Tweet.user_id))
                 .group_by(User.username)
                 .order_by(fn.COUNT(Tweet.id).desc()))
    """
    def __init__(self, name, arguments, coerce=True):
        self.name = name
        self.arguments = arguments
        if name and name.lower() in ('sum', 'count'):
            self._coerce = False
        else:
            self._coerce = coerce

    def __getattr__(self, attr):
        def decorator(*args):
            return Function(attr, args)
        return decorator

    def over(self, partition_by=None, order_by=None, start=None, end=None,
             window=None):
        """
        :param list partition_by: List of columns to partition by.
        :param list order_by: List of columns / expressions to order window by.
        :param start: A :py:class:`SQL` instance or a string expressing the
            start of the window range.
        :param end: A :py:class:`SQL` instance or a string expressing the
            end of the window range.
        :param Window window: A :py:class:`Window` instance.

        .. note::
            For simplicity, it is permissible to call ``over()`` with a
            :py:class:`Window` instance as the first and only parameter.

        Examples::

            # Using a simple partition on a single column.
            query = (Sample
                     .select(
                        Sample.counter,
                        Sample.value,
                        fn.AVG(Sample.value).over([Sample.counter]))
                     .order_by(Sample.counter))

            # Equivalent example Using a Window() instance instead.
            window = Window(partition_by=[Sample.counter])
            query = (Sample
                     .select(
                        Sample.counter,
                        Sample.value,
                        fn.AVG(Sample.value).over(window))
                     .window(window)  # Note call to ".window()"
                     .order_by(Sample.counter))

            # Example using bounded window.
            query = (Sample
                     .select(Sample.value,
                             fn.SUM(Sample.value).over(
                                partition_by=[Sample.counter],
                                start=Window.preceding(),  # unbounded.
                                end=Window.following(1)))  # 1 following.
                     .order_by(Sample.id))
        """
        if isinstance(partition_by, Window) and window is None:
            window = partition_by
        if start is not None and not isinstance(start, SQL):
            start = SQL(*start)
        if end is not None and not isinstance(end, SQL):
            end = SQL(*end)

        if window is None:
            node = Window(partition_by=partition_by, order_by=order_by,
                          start=start, end=end)
        else:
            node = SQL(window._alias)
        return NodeList((self, SQL('OVER'), node))

    def coerce(self, coerce=True):
        """
        :param bool coerce: Whether to coerce function-call result.
        """
        self._coerce = coerce
        return self

    def __sql__(self, ctx):
        ctx.literal(self.name)
        if not len(self.arguments):
            ctx.literal('()')
        else:
            # Special-case to avoid double-wrapping functions whose only
            # argument is a sub-query.
            if len(self.arguments) == 1 and isinstance(self.arguments[0],
                                                       SelectQuery):
                wrapper = CommaNodeList
            else:
                wrapper = EnclosedNodeList
            ctx.sql(wrapper([
                (argument if isinstance(argument, Node)
                 else Value(argument))
                for argument in self.arguments]))
        return ctx


fn = Function(None, None)


class Window(Node):
    """
    :param list partition_by: List of columns to partition by.
    :param list order_by: List of columns to order by.
    :param start: A :py:class:`SQL` instance or a string expressing the start
        of the window range.
    :param end: A :py:class:`SQL` instance or a string expressing the end of
        the window range.
    :param str alias: Alias for the window.

    Represent a WINDOW clause.
    """
    #: Constant for indicating current row for the start/end of range.
    CURRENT_ROW = 'CURRENT ROW'

    def __init__(self, partition_by=None, order_by=None, start=None, end=None,
                 alias=None):
        super(Window, self).__init__()
        self.partition_by = partition_by
        self.order_by = order_by
        self.start = start
        self.end = end
        if self.start is None and self.end is not None:
            raise ValueError('Cannot specify WINDOW end without start.')
        self._alias = alias or 'w'

    def alias(self, alias=None):
        """
        :param str alias: Alias to use for window.
        """
        self._alias = alias or 'w'
        return self

    @staticmethod
    def following(value=None):
        """
        :param value: Number of rows following. If ``None`` is UNBOUNDED.

        Convenience method for generating SQL suitable for passing in as the
        ``end`` parameter for a window range.
        """
        if value is None:
            return SQL('UNBOUNDED FOLLOWING')
        return SQL('%d FOLLOWING' % value)

    @staticmethod
    def preceding(value=None):
        """
        :param value: Number of rows preceding. If ``None`` is UNBOUNDED.

        Convenience method for generating SQL suitable for passing in as the
        ``start`` parameter for a window range.
        """
        if value is None:
            return SQL('UNBOUNDED PRECEDING')
        return SQL('%d PRECEDING' % value)

    def __sql__(self, ctx):
        if ctx.scope != SCOPE_SOURCE:
            ctx.literal(self._alias)
            ctx.literal(' AS ')

        with ctx(parentheses=True):
            parts = []
            if self.partition_by:
                parts.extend((
                    SQL('PARTITION BY'),
                    CommaNodeList(self.partition_by)))
            if self.order_by:
                parts.extend((
                    SQL('ORDER BY'),
                    CommaNodeList(self.order_by)))
            if self.start is not None and self.end is not None:
                parts.extend((
                    SQL('ROWS BETWEEN'),
                    self.start,
                    SQL('AND'),
                    self.end))
            elif self.start is not None:
                parts.extend((SQL('ROWS'), self.start))
            ctx.sql(NodeList(parts))
        return ctx

    def clone_base(self):
        return Window(self.partition_by, self.order_by)


def Case(predicate, expression_tuples, default=None):
    """
    :param predicate: Predicate for CASE query (optional).
    :param expression_tuples: One or more cases to evaluate.
    :param default: Default value (optional).
    :returns: Representation of CASE statement.

    Examples::

        Number = Table('numbers', ('val',))

        num_as_str = Case(Number.val, (
            (1, 'one'),
            (2, 'two'),
            (3, 'three')), 'a lot')

        query = Number.select(Number.val, num_as_str.alias('num_str'))

        # The above is equivalent to:
        # SELECT "val",
        #   CASE "val"
        #       WHEN 1 THEN 'one'
        #       WHEN 2 THEN 'two'
        #       WHEN 3 THEN 'three'
        #       ELSE 'a lot' END AS "num_str"
        # FROM "numbers"

        num_as_str = Case(None, (
            (Number.val == 1, 'one'),
            (Number.val == 2, 'two'),
            (Number.val == 3, 'three')), 'a lot')
        query = Number.select(Number.val, num_as_str.alias('num_str'))

        # The above is equivalent to:
        # SELECT "val",
        #   CASE
        #       WHEN "val" = 1 THEN 'one'
        #       WHEN "val" = 2 THEN 'two'
        #       WHEN "val" = 3 THEN 'three'
        #       ELSE 'a lot' END AS "num_str"
        # FROM "numbers"
    """
    clauses = [SQL('CASE')]
    if predicate is not None:
        clauses.append(predicate)
    for expr, value in expression_tuples:
        clauses.extend((SQL('WHEN'), expr, SQL('THEN'), value))
    if default is not None:
        clauses.extend((SQL('ELSE'), default))
    clauses.append(SQL('END'))
    return NodeList(clauses)


class NodeList(ColumnBase):
    """
    :param list nodes: Zero or more nodes.
    :param str glue: How to join the nodes when converting to SQL.
    :param bool parens: Whether to wrap the resulting SQL in parentheses.

    Represent a list of nodes, a multi-part clause, a list of parameters, etc.
    """
    def __init__(self, nodes, glue=' ', parens=False):
        self.nodes = nodes
        self.glue = glue
        self.parens = parens
        if parens and len(self.nodes) == 1:
            if isinstance(self.nodes[0], Expression):
                # Hack to avoid double-parentheses.
                self.nodes[0].flat = True

    def __sql__(self, ctx):
        n_nodes = len(self.nodes)
        if n_nodes == 0:
            return ctx
        with ctx(parentheses=self.parens):
            for i in range(n_nodes - 1):
                ctx.sql(self.nodes[i])
                ctx.literal(self.glue)
            ctx.sql(self.nodes[n_nodes - 1])
        return ctx


def CommaNodeList(nodes):
    """
    :param list nodes: Zero or more nodes.
    :returns: a :py:class:`NodeList`

    Represent a list of nodes joined by commas.
    """
    return NodeList(nodes, ', ')


def EnclosedNodeList(nodes):
    """
    :param list nodes: Zero or more nodes.
    :returns: a :py:class:`NodeList`

    Represent a list of nodes joined by commas and wrapped in parentheses.
    """
    return NodeList(nodes, ', ', True)


class DQ(ColumnBase):
    """
    :param query: Arbitrary filter expressions using Django-style lookups.

    Represent a composable Django-style filter expression suitable for use with
    the :py:meth:`Model.filter` or :py:meth:`ModelSelect.filter` methods.
    """
    def __init__(self, **query):
        super(DQ, self).__init__()
        self.query = query
        self._negated = False

    @Node.copy
    def __invert__(self):
        self._negated = not self._negated

    def clone(self):
        node = DQ(**self.query)
        node._negated = self._negated
        return node

#: Represent a row tuple.
Tuple = lambda *a: EnclosedNodeList(a)


class OnConflict(Node):
    """
    :param str action: Action to take when resolving conflict.
    :param update: A dictionary mapping column to new value.
    :param preserve: A list of columns whose values should be preserved.
    :param where: Expression to restrict the conflict resolution.
    :param conflict_target: Name of column or constraint to check.

    Represent a conflict resolution clause for a data-modification query.

    Depending on the database-driver being used, one or more of the above
    parameters may be required.
    """
    def __init__(self, action=None, update=None, preserve=None, where=None,
                 conflict_target=None):
        self._action = action
        self._update = update
        self._preserve = ensure_tuple(preserve)
        self._where = where
        self._conflict_target = ensure_tuple(conflict_target)

    def get_conflict_statement(self, ctx):
        return ctx.state.conflict_statement(self)

    def get_conflict_update(self, ctx):
        return ctx.state.conflict_update(self)

    @Node.copy
    def preserve(self, *columns):
        """
        :param columns: Columns whose values should be preserved.
        """
        self._preserve = columns

    @Node.copy
    def update(self, _data=None, **kwargs):
        """
        :param dict _data: Dictionary mapping column to new value.
        :param kwargs: Dictionary mapping column name to new value.

        The ``update()`` method supports being called with either a dictionary
        of column-to-value, **or** keyword arguments representing the same.
        """
        if _data and kwargs and not isinstance(_data, dict):
            raise ValueError('Cannot mix data with keyword arguments in the '
                             'OnConflict update method.')
        _data = _data or {}
        if kwargs:
            _data.update(kwargs)
        self._update = _data

    @Node.copy
    def where(self, *expressions):
        """
        :param expressions: Expressions that restrict the action of the
            conflict resolution clause.
        """
        if self._where is not None:
            expressions = (self._where,) + expressions
        self._where = reduce(operator.and_, expressions)

    @Node.copy
    def conflict_target(self, *constraints):
        """
        :param constraints: Name(s) of columns/constraints that are the target
            of the conflict resolution.
        """
        self._conflict_target = constraints


def database_required(method):
    @wraps(method)
    def inner(self, database=None, *args, **kwargs):
        database = self._database if database is None else database
        if not database:
            raise Exception('Query must be bound to a database in order '
                            'to call "%s".' % method.__name__)
        return method(self, database, *args, **kwargs)
    return inner

# BASE QUERY INTERFACE.

class BaseQuery(Node):
    """
    Base-class implementing common query methods.
    """
    default_row_type = ROW.DICT

    def __init__(self, _database=None, **kwargs):
        self._database = _database
        self._cursor_wrapper = None
        self._row_type = None
        self._constructor = None
        super(BaseQuery, self).__init__(**kwargs)

    def bind(self, database=None):
        """
        :param Database database: Database to execute query against.

        Bind the query to the given database for execution.
        """
        self._database = database
        return self

    def clone(self):
        query = super(BaseQuery, self).clone()
        query._cursor_wrapper = None
        return query

    def dicts(self, as_dict=True):
        """
        :param bool as_dict: Specify whether to return rows as dictionaries.

        Return rows as dictionaries.
        """
        self._row_type = ROW.DICT if as_dict else None
        return self

    def tuples(self, as_tuple=True):
        """
        :param bool as_tuple: Specify whether to return rows as tuples.

        Return rows as tuples.
        """
        self._row_type = ROW.TUPLE if as_tuple else None
        return self

    def namedtuples(self, as_namedtuple=True):
        """
        :param bool as_namedtuple: Specify whether to return rows as named
            tuples.

        Return rows as named tuples.
        """
        self._row_type = ROW.NAMED_TUPLE if as_namedtuple else None
        return self

    def objects(self, constructor=None):
        """
        :param constructor: Function that accepts row dict and returns an
            arbitrary object.

        Return rows as arbitrary objects using the given constructor.
        """
        self._row_type = ROW.CONSTRUCTOR if constructor else None
        self._constructor = constructor
        return self

    def _get_cursor_wrapper(self, cursor):
        row_type = self._row_type or self.default_row_type

        if row_type == ROW.DICT:
            return DictCursorWrapper(cursor)
        elif row_type == ROW.TUPLE:
            return CursorWrapper(cursor)
        elif row_type == ROW.NAMED_TUPLE:
            return NamedTupleCursorWrapper(cursor)
        elif row_type == ROW.CONSTRUCTOR:
            return ObjectCursorWrapper(cursor, self._constructor)
        else:
            raise ValueError('Unrecognized row type: "%s".' % row_type)

    def __sql__(self, ctx):
        raise NotImplementedError

    def sql(self):
        """
        :returns: A 2-tuple consisting of the query's SQL and parameters.
        """
        if self._database:
            context = self._database.get_sql_context()
        else:
            context = Context()
        return context.parse(self)

    @database_required
    def execute(self, database):
        """
        :param Database database: Database to execute query against. Not
            required if query was previously bound to a database.

        Execute the query and return result (depends on type of query being
        executed).
        """
        return self._execute(database)

    def _execute(self, database):
        raise NotImplementedError

    def iterator(self, database=None):
        """
        :param Database database: Database to execute query against. Not
            required if query was previously bound to a database.

        Execute the query and return an iterator over the result-set. For large
        result-sets this method is preferable as rows are not cached in-memory
        during iteration.

        .. note::
            Because rows are not cached, the query may only be iterated over
            once. Subsequent iterations will return empty result-sets as the
            cursor will have been consumed.
        """
        return iter(self.execute(database).iterator())

    def _ensure_execution(self):
        if not self._cursor_wrapper:
            if not self._database:
                raise ValueError('Query has not been executed.')
            self.execute()

    def __iter__(self):
        """
        Execute the query and return an iterator over the result-set.

        Unlike :py:meth:`~BaseQuery.iterator`, this method will cause rows to
        be cached in order to allow efficient iteration, indexing and slicing.
        """
        self._ensure_execution()
        return iter(self._cursor_wrapper)

    def __getitem__(self, value):
        """
        :param value: Either an integer index or a slice.

        Retrieve a row or range of rows from the result-set.
        """
        self._ensure_execution()
        if isinstance(value, slice):
            index = value.stop
        else:
            index = value
        if index is not None and index >= 0:
            index += 1
        self._cursor_wrapper.fill_cache(index)
        return self._cursor_wrapper.row_cache[value]

    def __len__(self):
        """
        Return the number of rows in the result-set.

        .. warning::
            This does not issue a ``COUNT()`` query. Instead, the result-set
            is loaded as it would be during normal iteration, and the length
            is determined from the size of the result set.
        """
        self._ensure_execution()
        return len(self._cursor_wrapper)


class RawQuery(BaseQuery):
    """
    :param str sql: SQL query.
    :param tuple params: Parameters (optional).

    Create a query by directly specifying the SQL to execute.
    """
    def __init__(self, sql=None, params=None, **kwargs):
        super(RawQuery, self).__init__(**kwargs)
        self._sql = sql
        self._params = params

    def __sql__(self, ctx):
        ctx.literal(self._sql)
        if self._params:
            for param in self._params:
                if isinstance(param, Node):
                    ctx.sql(param)
                else:
                    ctx.value(param)
        return ctx

    def _execute(self, database):
        if self._cursor_wrapper is None:
            cursor = database.execute(self)
            self._cursor_wrapper = self._get_cursor_wrapper(cursor)
        return self._cursor_wrapper


class Query(BaseQuery):
    """
    :param where: Representation of WHERE clause.
    :param tuple order_by: Columns or values to order by.
    :param int limit: Value of LIMIT clause.
    :param int offset: Value of OFFSET clause.

    Base-class for queries that support method-chaining APIs.
    """
    def __init__(self, where=None, order_by=None, limit=None, offset=None,
                 **kwargs):
        super(Query, self).__init__(**kwargs)
        self._where = where
        self._order_by = order_by
        self._limit = limit
        self._offset = offset

        self._cte_list = None

    @Node.copy
    def with_cte(self, *cte_list):
        """
        :param cte_list: zero or more CTE objects.

        Include the given common-table-expressions in the query. Any previously
        specified CTEs will be overwritten.
        """
        self._cte_list = cte_list

    @Node.copy
    def where(self, *expressions):
        """
        :param expressions: zero or more expressions to include in the WHERE
            clause.

        Include the given expressions in the WHERE clause of the query. The
        expressions will be AND-ed together with any previously-specified
        WHERE expressions.
        """
        if self._where is not None:
            expressions = (self._where,) + expressions
        self._where = reduce(operator.and_, expressions)

    @Node.copy
    def order_by(self, *values):
        """
        :param values: zero or more Column-like objects to order by.

        Define the ORDER BY clause. Any previously-specified values will be
        overwritten.
        """
        self._order_by = values

    @Node.copy
    def order_by_extend(self, *values):
        """
        :param values: zero or more Column-like objects to order by.

        Extend any previously-specified ORDER BY clause with the given values.
        """
        self._order_by = ((self._order_by or ()) + values) or None

    @Node.copy
    def limit(self, value=None):
        """
        :param int value: specify value for LIMIT clause.
        """
        self._limit = value

    @Node.copy
    def offset(self, value=None):
        """
        :param int value: specify value for OFFSET clause.
        """
        self._offset = value

    @Node.copy
    def paginate(self, page, paginate_by=20):
        """
        :param int page: Page number of results (starting from 1).
        :param int paginate_by: Rows-per-page.

        Convenience method for specifying the LIMIT and OFFSET in a more
        intuitive way.
        """
        if page > 0:
            page -= 1
        self._limit = paginate_by
        self._offset = page * paginate_by

    def _apply_ordering(self, ctx):
        if self._order_by:
            (ctx
             .literal(' ORDER BY ')
             .sql(CommaNodeList(self._order_by)))
        if self._limit is not None or (self._offset is not None and
                                       ctx.state.limit_max):
            ctx.literal(' LIMIT %d' % (self._limit or ctx.state.limit_max))
        if self._offset is not None:
            ctx.literal(' OFFSET %d' % self._offset)
        return ctx

    def __sql__(self, ctx):
        if self._cte_list:
            # The CTE scope is only used at the very beginning of the query,
            # when we are describing the various CTEs we will be using.
            recursive = any(cte._recursive for cte in self._cte_list)
            with ctx.scope_cte():
                (ctx
                 .literal('WITH RECURSIVE ' if recursive else 'WITH ')
                 .sql(CommaNodeList(self._cte_list))
                 .literal(' '))
        return ctx


def __compound_select__(operation, inverted=False):
    def method(self, other):
        if inverted:
            self, other = other, self
        return CompoundSelectQuery(self, operation, other)
    return method


class SelectQuery(Query):
    """
    Select query helper-class that implements operator-overloads for creating
    compound queries.
    """
    __add__ = __compound_select__('UNION ALL')
    __or__ = __compound_select__('UNION')
    __and__ = __compound_select__('INTERSECT')
    __sub__ = __compound_select__('EXCEPT')
    __radd__ = __compound_select__('UNION ALL', inverted=True)
    __ror__ = __compound_select__('UNION', inverted=True)
    __rand__ = __compound_select__('INTERSECT', inverted=True)
    __rsub__ = __compound_select__('EXCEPT', inverted=True)

    def cte(self, name, recursive=False, columns=None):
        return CTE(name, self, recursive=recursive, columns=None)


class SelectBase(_HashableSource, Source, SelectQuery):
    """
    Base-class for :py:class:`Select` and :py:class:`CompoundSelect` queries.
    """
    def _get_hash(self):
        return hash((self.__class__, self._alias or id(self)))

    def _execute(self, database):
        if self._cursor_wrapper is None:
            cursor = database.execute(self)
            self._cursor_wrapper = self._get_cursor_wrapper(cursor)
        return self._cursor_wrapper

    @database_required
    def peek(self, database, n=1):
        """
        :param Database database: database to execute query against.
        :param int n: Number of rows to return.
        :returns: A single row if n = 1, else a list of rows.

        Execute the query and return the given number of rows from the start
        of the cursor. This function may be called multiple times safely, and
        will always return the first N rows of results.
        """
        rows = self.execute(database)[:n]
        if rows:
            return rows[0] if n == 1 else rows

    @database_required
    def first(self, database, n=1):
        """
        :param Database database: database to execute query against.
        :param int n: Number of rows to return.
        :returns: A single row if n = 1, else a list of rows.

        Like the :py:meth:`~SelectBase.peek` method, except a ``LIMIT`` is
        applied to the query to ensure that only ``n`` rows are returned.
        Multiple calls for the same value of ``n`` will not result in multiple
        executions.
        """
        if self._limit != n:
            self._limit = n
            self._cursor_wrapper = None
        return self.peek(database, n=n)

    @database_required
    def scalar(self, database, as_tuple=False):
        """
        :param Database database: database to execute query against.
        :param bool as_tuple: Return the result as a tuple?
        :returns: Single scalar value if ``as_tuple = False``, else row tuple.

        Return a scalar value from the first row of results. If multiple
        scalar values are anticipated (e.g. multiple aggregations in a single
        query) then you may specify ``as_tuple=True`` to get the row tuple.

        Example::

            query = Note.select(fn.MAX(Note.timestamp))
            max_ts = query.scalar(db)

            query = Note.select(fn.MAX(Note.timestamp), fn.COUNT(Note.id))
            max_ts, n_notes = query.scalar(db, as_tuple=True)
        """
        row = self.tuples().peek(database)
        return row[0] if row and not as_tuple else row

    @database_required
    def count(self, database, clear_limit=False):
        """
        :param Database database: database to execute query against.
        :param bool clear_limit: Clear any LIMIT clause when counting.
        :return: Number of rows in the query result-set.

        Return number of rows in the query result-set.

        Implemented by running SELECT COUNT(1) FROM (<current query>).
        """
        clone = self.order_by().alias('_wrapped')
        if clear_limit:
            clone._limit = clone._offset = None
        if clone._having is None and clone._windows is None:
            clone = clone.select(SQL('1'))
        return Select([clone], [fn.COUNT(SQL('1'))]).scalar(database)

    @database_required
    def exists(self, database):
        """
        :param Database database: database to execute query against.
        :return: Whether any results exist for the current query.

        Return a boolean indicating whether the current query has any results.
        """
        clone = self.columns(SQL('1'))
        clone._limit = 1
        clone._offset = None
        return bool(clone.scalar())

    @database_required
    def get(self, database):
        """
        :param Database database: database to execute query against.
        :return: A single row from the database or ``None``.

        Execute the query and return the first row, if it exists. Multiple
        calls will result in multiple queries being executed.
        """
        self._cursor_wrapper = None
        try:
            return self.execute(database)[0]
        except IndexError:
            pass


# QUERY IMPLEMENTATIONS.


class CompoundSelectQuery(SelectBase):
    """
    :param SelectBase lhs: A Select or CompoundSelect query.
    :param str op: Operation (e.g. UNION, INTERSECT, EXCEPT).
    :param SelectBase rhs: A Select or CompoundSelect query.

    Class representing a compound SELECT query.
    """
    def __init__(self, lhs, op, rhs):
        super(CompoundSelectQuery, self).__init__()
        self.lhs = lhs
        self.op = op
        self.rhs = rhs

    def _get_query_key(self):
        return (self.lhs.get_query_key(), self.rhs.get_query_key())

    def __sql__(self, ctx):
        if ctx.scope == SCOPE_COLUMN:
            return self.apply_column(ctx)

        parens_around_query = ctx.state.compound_select_parentheses
        outer_parens = ctx.subquery or (ctx.scope == SCOPE_SOURCE)
        with ctx(parentheses=outer_parens):
            with ctx.scope_normal(parentheses=parens_around_query,
                                  subquery=False):
                ctx.sql(self.lhs)
            ctx.literal(' %s ' % self.op)
            with ctx.push_alias():
                with ctx.scope_normal(parentheses=parens_around_query,
                                      subquery=False):
                    ctx.sql(self.rhs)

        # Apply ORDER BY, LIMIT, OFFSET.
        self._apply_ordering(ctx)
        return self.apply_alias(ctx)


class Select(SelectBase):
    """
    :param list from_list: List of sources for FROM clause.
    :param list columns: Columns or values to select.
    :param list group_by: List of columns or values to group by.
    :param Expression having: Expression for HAVING clause.
    :param distinct: Either a boolean or a list of column-like objects.
    :param list windows: List of :py:class:`Window` clauses.
    :param for_update: Boolean or str indicating if SELECT...FOR UPDATE.

    Class representing a SELECT query.

    .. note::
        While it is possible to instantiate the query, more commonly you will
        build the query using the method-chaining APIs.
    """
    def __init__(self, from_list=None, columns=None, group_by=None,
                 having=None, distinct=None, windows=None, for_update=None,
                 **kwargs):
        super(Select, self).__init__(**kwargs)
        self._from_list = (list(from_list) if isinstance(from_list, tuple)
                           else from_list) or []
        self._returning = columns
        self._group_by = group_by
        self._having = having
        self._windows = None
        self._for_update = 'FOR UPDATE' if for_update is True else for_update

        self._distinct = self._simple_distinct = None
        if distinct:
            if isinstance(distinct, bool):
                self._simple_distinct = distinct
            else:
                self._distinct = distinct

        self._cursor_wrapper = None

    @Node.copy
    def columns(self, *columns):
        """
        :param columns: Zero or more column-like objects to SELECT.

        Specify which columns or column-like values to SELECT.
        """
        self._returning = columns
    select = columns

    @Node.copy
    def from_(self, *sources):
        """
        :param sources: Zero or more sources for the FROM clause.

        Specify which table-like objects should be used in the FROM clause.
        """
        self._from_list = list(sources)

    @Node.copy
    def join(self, dest, join_type='INNER', on=None):
        """
        :param dest: A table or table-like object.
        :param str join_type: Type of JOIN, default is "INNER".
        :param Expression on: Join predicate.

        Express a JOIN::

            User = Table('users', ('id', 'username'))
            Note = Table('notes', ('id', 'user_id', 'content'))

            query = (Note
                     .select(Note.content, User.username)
                     .join(User, on=(Note.user_id == User.id)))
        """
        if not self._from_list:
            raise ValueError('No sources to join on.')
        item = self._from_list.pop()
        self._from_list.append(Join(item, dest, join_type, on))

    @Node.copy
    def group_by(self, *columns):
        """
        :param values: zero or more Column-like objects to order by.

        Define the GROUP BY clause. Any previously-specified values will be
        overwritten.
        """
        self._group_by = columns

    @Node.copy
    def group_by_extend(self, *values):
        """
        :param values: zero or more Column-like objects to order by.

        Extend the GROUP BY clause.
        """
        self._group_by = ((self._group_by or ()) + values) or None

    @Node.copy
    def having(self, *expressions):
        """
        :param expressions: zero or more expressions to include in the HAVING
            clause.

        Include the given expressions in the HAVING clause of the query. The
        expressions will be AND-ed together with any previously-specified
        HAVING expressions.
        """
        if self._having is not None:
            expressions = (self._having,) + expressions
        self._having = reduce(operator.and_, expressions)

    @Node.copy
    def distinct(self, *columns):
        """
        :param columns: Zero or more column-like objects.

        Indicate whether this query should use a DISTINCT clause. By specifying
        a single value of ``True`` the query will use a simple SELECT DISTINCT.
        Specifying one or more columns will result in a SELECT DISTINCT ON.
        """
        if len(columns) == 1 and (columns[0] is True or columns[0] is False):
            self._simple_distinct = columns[0]
        else:
            self._simple_distinct = False
            self._distinct = columns

    @Node.copy
    def window(self, *windows):
        """
        :param windows: zero or more :py:class:`Window` objects.

        Define the WINDOW clause. Any previously-specified values will be
        overwritten.
        """
        self._windows = windows if windows else None

    @Node.copy
    def for_update(self, for_update=True):
        """
        :param for_update: Either a boolean or a string indicating the
            desired expression, e.g. "FOR UPDATE NOWAIT".
        """
        self._for_update = 'FOR UPDATE' if for_update is True else for_update

    def _get_query_key(self):
        return self._alias

    def __sql_selection__(self, ctx, is_subquery=False):
        return ctx.sql(CommaNodeList(self._returning))

    def __sql__(self, ctx):
        super(Select, self).__sql__(ctx)
        if ctx.scope == SCOPE_COLUMN:
            return self.apply_column(ctx)

        is_subquery = ctx.subquery
        parentheses = is_subquery or (ctx.scope == SCOPE_SOURCE)

        with ctx.scope_normal(converter=None, parentheses=parentheses,
                              subquery=True):
            ctx.literal('SELECT ')
            if self._simple_distinct or self._distinct is not None:
                ctx.literal('DISTINCT ')
                if self._distinct:
                    (ctx
                     .literal('ON ')
                     .sql(EnclosedNodeList(self._distinct))
                     .literal(' '))

            with ctx.scope_source():
                ctx = self.__sql_selection__(ctx, is_subquery)

            if self._from_list:
                with ctx.scope_source(parentheses=False):
                    ctx.literal(' FROM ').sql(CommaNodeList(self._from_list))

            if self._where is not None:
                ctx.literal(' WHERE ').sql(self._where)

            if self._group_by:
                ctx.literal(' GROUP BY ').sql(CommaNodeList(self._group_by))

            if self._having is not None:
                ctx.literal(' HAVING ').sql(self._having)

            if self._windows is not None:
                ctx.literal(' WINDOW ')
                ctx.sql(CommaNodeList(self._windows))

            # Apply ORDER BY, LIMIT, OFFSET.
            self._apply_ordering(ctx)

            if self._for_update:
                if not ctx.state.for_update:
                    raise ValueError('FOR UPDATE specified but not supported '
                                     'by database.')
                ctx.literal(' ')
                ctx.sql(SQL(self._for_update))

        ctx = self.apply_alias(ctx)
        return ctx


class _WriteQuery(Query):
    """
    :param Table table: Table to write to.
    :param list returning: List of columns for RETURNING clause.

    Base-class for write queries.
    """
    def __init__(self, table, returning=None, **kwargs):
        self.table = table
        self._returning = returning
        super(_WriteQuery, self).__init__(**kwargs)

    @Node.copy
    def returning(self, *returning):
        """
        :param returning: Zero or more column-like objects for RETURNING clause

        Specify the RETURNING clause of query (if supported by your database).
        """
        self._returning = returning

    @property
    def _empty_returning(self):
        return self._returning and len(self._returning) == 1 and \
                self._returning[0] is None

    def apply_returning(self, ctx):
        if self._returning and not self._empty_returning:
            ctx.literal(' RETURNING ').sql(CommaNodeList(self._returning))
        return ctx

    def _execute(self, database):
        if self._returning and not self._empty_returning:
            cursor = self.execute_returning(database)
        else:
            cursor = database.execute(self)
        return self.handle_result(database, cursor)

    def execute_returning(self, database):
        if self._cursor_wrapper is None:
            cursor = database.execute(self)
            self._cursor_wrapper = self._get_cursor_wrapper(cursor)
        return self._cursor_wrapper

    def handle_result(self, database, cursor):
        if self._returning:
            return cursor
        return database.rows_affected(cursor)

    def _set_table_alias(self, ctx):
        ctx.alias_manager[self.table] = self.table.__name__

    def __sql__(self, ctx):
        super(_WriteQuery, self).__sql__(ctx)
        # We explicitly set the table alias to the table's name, which ensures
        # that if a sub-select references a column on the outer table, we won't
        # assign it a new alias (e.g. t2) but will refer to it as table.column.
        self._set_table_alias(ctx)
        return ctx


class Update(_WriteQuery):
    """
    :param Table table: Table to update.
    :param dict update: Data to update.

    Class representing an UPDATE query.

    Example::

        PageView = Table('page_views')
        query = (PageView
                 .update({PageView.c.page_views: PageView.c.page_views + 1})
                 .where(PageView.c.url == url))
        query.execute(database)
    """
    def __init__(self, table, update=None, **kwargs):
        super(Update, self).__init__(table, **kwargs)
        self._update = update

    def __sql__(self, ctx):
        super(Update, self).__sql__(ctx)

        with ctx.scope_values(subquery=True):
            ctx.literal('UPDATE ')

            expressions = []
            for k, v in sorted(self._update.items(), key=ctx.column_sort_key):
                if not isinstance(v, Node):
                    converter = k.db_value if isinstance(k, Field) else None
                    v = Value(v, converter=converter, unpack=False)
                expressions.append(NodeList((k, SQL('='), v)))

            (ctx
             .sql(self.table)
             .literal(' SET ')
             .sql(CommaNodeList(expressions)))

            if self._where:
                ctx.literal(' WHERE ').sql(self._where)
            self._apply_ordering(ctx)
            return self.apply_returning(ctx)


class Insert(_WriteQuery):
    """
    :param Table table: Table to INSERT data into.
    :param insert: Either a dict, a list, or a query.
    :param list columns: List of columns when ``insert`` is a list or query.
    :param on_conflict: Conflict resolution strategy.

    Class representing an INSERT query.
    """
    SIMPLE = 0
    QUERY = 1
    MULTI = 2
    class DefaultValuesException(Exception): pass

    def __init__(self, table, insert=None, columns=None, on_conflict=None,
                 **kwargs):
        super(Insert, self).__init__(table, **kwargs)
        self._insert = insert
        self._columns = columns
        self._on_conflict = on_conflict
        self._query_type = None

    def where(self, *expressions):
        raise NotImplementedError('INSERT queries cannot have a WHERE clause.')

    @Node.copy
    def on_conflict_ignore(self, ignore=True):
        """
        :param bool ignore: Whether to add ON CONFLICT IGNORE clause.

        Specify IGNORE conflict resolution strategy.
        """
        self._on_conflict = OnConflict('IGNORE') if ignore else None

    @Node.copy
    def on_conflict_replace(self, replace=True):
        """
        :param bool ignore: Whether to add ON CONFLICT REPLACE clause.

        Specify REPLACE conflict resolution strategy.
        """
        self._on_conflict = OnConflict('REPLACE') if replace else None

    @Node.copy
    def on_conflict(self, *args, **kwargs):
        """
        Specify an ON CONFLICT clause by populating a :py:class:`OnConflict`
        object.
        """
        self._on_conflict = (OnConflict(*args, **kwargs) if (args or kwargs)
                             else None)

    def _simple_insert(self, ctx):
        if not self._insert:
            raise self.DefaultValuesException
        columns = []
        values = []
        for k, v in sorted(self._insert.items(), key=ctx.column_sort_key):
            columns.append(k)
            if not isinstance(v, Node):
                converter = k.db_value if isinstance(k, Field) else None
                v = Value(v, converter=converter, unpack=False)
            values.append(v)
        return (ctx
                .sql(EnclosedNodeList(columns))
                .literal(' VALUES ')
                .sql(EnclosedNodeList(values)))

    def _multi_insert(self, ctx):
        rows_iter = iter(self._insert)
        columns = self._columns
        if not columns:
            try:
                row = next(rows_iter)
            except StopIteration:
                raise self.DefaultValuesException('Error: no rows to insert.')
            else:
                accum = []
                value_lookups = {}
                for key in row:
                    if isinstance(key, basestring):
                        column = getattr(self.table, key)
                    else:
                        column = key
                    accum.append(column)
                    value_lookups[column] = key

            columns = sorted(accum, key=lambda obj: obj.get_sort_key(ctx))
            rows_iter = itertools.chain(iter((row,)), rows_iter)
        else:
            value_lookups = dict((column, column) for column in columns)

        ctx.sql(EnclosedNodeList(columns)).literal(' VALUES ')
        columns_converters = [
            (column, column.db_value if isinstance(column, Field) else None)
            for column in columns]

        all_values = []
        for row in rows_iter:
            values = []
            for column, converter in columns_converters:
                val = row[value_lookups[column]]
                if not isinstance(val, Node):
                    val = Value(val, converter=converter, unpack=False)
                values.append(val)

            all_values.append(EnclosedNodeList(values))

        return ctx.sql(CommaNodeList(all_values))

    def _query_insert(self, ctx):
        return (ctx
                .sql(EnclosedNodeList(self._columns))
                .literal(' ')
                .sql(self._insert))

    def _default_values(self, ctx):
        if not self._database:
            return ctx.literal('DEFAULT VALUES')
        return self._database.default_values_insert(ctx)

    def __sql__(self, ctx):
        super(Insert, self).__sql__(ctx)
        with ctx.scope_values():
            statement = None
            if self._on_conflict is not None:
                statement = self._on_conflict.get_conflict_statement(ctx)

            (ctx
             .sql(statement or SQL('INSERT'))
             .literal(' INTO ')
             .sql(self.table)
             .literal(' '))

            if isinstance(self._insert, dict) and not self._columns:
                try:
                    self._simple_insert(ctx)
                except self.DefaultValuesException:
                    self._default_values(ctx)
                self._query_type = Insert.SIMPLE
            elif isinstance(self._insert, SelectQuery):
                self._query_insert(ctx)
                self._query_type = Insert.QUERY
            else:
                try:
                    self._multi_insert(ctx)
                except self.DefaultValuesException:
                    return
                self._query_type = Insert.MULTI

            if self._on_conflict is not None:
                update = self._on_conflict.get_conflict_update(ctx)
                if update is not None:
                    ctx.literal(' ').sql(update)

            return self.apply_returning(ctx)

    def _execute(self, database):
        if not self._returning and database.returning_clause:
            self._returning = (self.table.primary_key,)
        return super(Insert, self)._execute(database)

    def handle_result(self, database, cursor):
        return database.last_insert_id(cursor, self._query_type)


class Delete(_WriteQuery):
    """
    Class representing a DELETE query.
    """
    def __sql__(self, ctx):
        super(Delete, self).__sql__(ctx)

        with ctx.scope_values(subquery=True):
            ctx.literal('DELETE FROM ').sql(self.table)
            if self._where is not None:
                ctx.literal(' WHERE ').sql(self._where)

            self._apply_ordering(ctx)
            return self.apply_returning(ctx)


class Index(Node):
    """
    :param str name: Index name.
    :param Table table: Table to create index on.
    :param expressions: List of columns to index on (or expressions).
    :param bool unique: Whether index is UNIQUE.
    :param bool safe: Whether to add IF NOT EXISTS clause.
    :param Expression where: Optional WHERE clause for index.
    :param str using: Index algorithm.
    """
    def __init__(self, name, table, expressions, unique=False, safe=False,
                 where=None, using=None):
        self._name = name
        self._table = Entity(table) if not isinstance(table, Table) else table
        self._expressions = expressions
        self._where = where
        self._unique = unique
        self._safe = safe
        self._using = using

    @Node.copy
    def safe(self, _safe=True):
        """
        :param bool _safe: Whether to add IF NOT EXISTS clause.
        """
        self._safe = _safe

    @Node.copy
    def where(self, *expressions):
        """
        :param expressions: zero or more expressions to include in the WHERE
            clause.

        Include the given expressions in the WHERE clause of the index. The
        expressions will be AND-ed together with any previously-specified
        WHERE expressions.
        """
        if self._where is not None:
            expressions = (self._where,) + expressions
        self._where = reduce(operator.and_, expressions)

    @Node.copy
    def using(self, _using=None):
        """
        :param str _using: Specify index algorithm for USING clause.
        """
        self._using = _using

    def __sql__(self, ctx):
        statement = 'CREATE UNIQUE INDEX ' if self._unique else 'CREATE INDEX '
        with ctx.scope_values(subquery=True):
            ctx.literal(statement)
            if self._safe:
                ctx.literal('IF NOT EXISTS ')
            (ctx
             .sql(Entity(self._name))
             .literal(' ON ')
             .sql(self._table)
             .literal(' '))
            if self._using is not None:
                ctx.literal('USING %s ' % self._using)

            ctx.sql(EnclosedNodeList([
                SQL(expr) if isinstance(expr, basestring) else expr
                for expr in self._expressions]))
            if self._where is not None:
                ctx.literal(' WHERE ').sql(self._where)

        return ctx


class ModelIndex(Index):
    """
    :param Model model: Model class to create index on.
    :param list fields: Fields to index.
    :param bool unique: Whether index is UNIQUE.
    :param bool safe: Whether to add IF NOT EXISTS clause.
    :param Expression where: Optional WHERE clause for index.
    :param str using: Index algorithm.
    :param str name: Optional index name.
    """
    def __init__(self, model, fields, unique=False, safe=True, where=None,
                 using=None, name=None):
        self._model = model
        if name is None:
            name = self._generate_name_from_fields(model, fields)
        if using is None:
            for field in fields:
                if getattr(field, 'index_type', None):
                    using = field.index_type
        super(ModelIndex, self).__init__(
            name=name,
            table=model._meta.table,
            expressions=fields,
            unique=unique,
            safe=safe,
            where=where,
            using=using)

    def _generate_name_from_fields(self, model, fields):
        accum = []
        for field in fields:
            if isinstance(field, basestring):
                accum.append(field.split()[0])
            else:
                if isinstance(field, Node) and not isinstance(field, Field):
                    field = field.unwrap()
                if isinstance(field, Field):
                    accum.append(field.column_name)

        if not accum:
            raise ValueError('Unable to generate a name for the index, please '
                             'explicitly specify a name.')

        index_name = re.sub('[^\w]+', '',
                            '%s_%s' % (model._meta.name, '_'.join(accum)))
        if len(index_name) > 64:
            index_hash = hashlib.md5(index_name.encode('utf-8')).hexdigest()
            index_name = '%s_%s' % (index_name[:56], index_hash[:7])
        return index_name



# DB-API 2.0 EXCEPTIONS.


class PeeweeException(Exception): pass
class ImproperlyConfigured(PeeweeException): pass
class DatabaseError(PeeweeException): pass
class DataError(DatabaseError): pass
class IntegrityError(DatabaseError): pass
class InterfaceError(PeeweeException): pass
class InternalError(DatabaseError): pass
class NotSupportedError(DatabaseError): pass
class OperationalError(DatabaseError): pass
class ProgrammingError(DatabaseError): pass


class ExceptionWrapper(object):
    __slots__ = ('exceptions',)
    def __init__(self, exceptions):
        self.exceptions = exceptions
    def __enter__(self): pass
    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            return
        if exc_type.__name__ in self.exceptions:
            new_type = self.exceptions[exc_type.__name__]
            exc_args = exc_value if PY26 else exc_value.args
            reraise(new_type, new_type(*exc_args), traceback)

EXCEPTIONS = {
    'ConstraintError': IntegrityError,
    'DatabaseError': DatabaseError,
    'DataError': DataError,
    'IntegrityError': IntegrityError,
    'InterfaceError': InterfaceError,
    'InternalError': InternalError,
    'NotSupportedError': NotSupportedError,
    'OperationalError': OperationalError,
    'ProgrammingError': ProgrammingError}

__exception_wrapper__ = ExceptionWrapper(EXCEPTIONS)


# DATABASE INTERFACE AND CONNECTION MANAGEMENT.


IndexMetadata = namedtuple(
    'IndexMetadata',
    ('name', 'sql', 'columns', 'unique', 'table'))
ColumnMetadata = namedtuple(
    'ColumnMetadata',
    ('name', 'data_type', 'null', 'primary_key', 'table'))
ForeignKeyMetadata = namedtuple(
    'ForeignKeyMetadata',
    ('column', 'dest_table', 'dest_column', 'table'))


class _ConnectionState(object):
    def __init__(self, **kwargs):
        super(_ConnectionState, self).__init__(**kwargs)
        self.reset()

    def reset(self):
        self.closed = True
        self.conn = None
        self.transactions = []

    def set_connection(self, conn):
        self.conn = conn
        self.closed = False


class _ConnectionLocal(_ConnectionState, threading.local): pass
class _NoopLock(object):
    __slots__ = ()
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass


class ConnectionContext(_callable_context_manager):
    __slots__ = ('db',)
    def __init__(self, db): self.db = db
    def __enter__(self):
        if self.db.is_closed():
            self.db.connect()
    def __exit__(self, exc_type, exc_val, exc_tb): self.db.close()


class Database(_callable_context_manager):
    """
    :param str database: Database name or filename for SQLite.
    :param bool thread_safe: Whether to store connection state in a
        thread-local.
    :param bool autorollback: Automatically rollback queries that fail when
        **not** in an explicit transaction.
    :param dict field_types: A mapping of additional field types to support.
    :param dict operations: A mapping of additional operations to support.
    :param kwargs: Arbitrary keyword arguments that will be passed to the
        database driver when a connection is created, for example ``password``,
        ``host``, etc.

    The :py:class:`Database` is responsible for:

    * Executing queries
    * Managing connections
    * Transactions
    * Introspection

    .. note::

        The database can be instantiated with ``None`` as the database name if
        the database is not known until run-time. In this way you can create a
        database instance and then configure it elsewhere when the settings are
        known. This is called *deferred* initialization.

        To initialize a database that has been *deferred*, use the
        :py:meth:`~Database.init` method.
    """
    context_class = Context
    field_types = {}
    operations = {}

    #: String used as parameter placeholder in SQL queries.
    param = '?'

    #: Type of quote-mark used to denote entities such as tables or columns.
    quote = '"'

    # Feature toggles.
    commit_select = False
    compound_select_parentheses = False
    for_update = False
    limit_max = None
    returning_clause = False
    safe_create_index = True
    safe_drop_index = True
    sequences = False

    def __init__(self, database, thread_safe=True, autorollback=False,
                 field_types=None, operations=None, **kwargs):
        self._field_types = merge_dict(FIELD, self.field_types)
        self._operations = merge_dict(OP, self.operations)
        if field_types:
            self._field_types.update(field_types)
        if operations:
            self._operations.update(operations)

        self.autorollback = autorollback
        self.thread_safe = thread_safe
        if thread_safe:
            self._state = _ConnectionLocal()
            self._lock = threading.Lock()
        else:
            self._state = _ConnectionState()
            self._lock = _NoopLock()

        self.connect_params = {}
        self.init(database, **kwargs)

    def init(self, database, **kwargs):
        """
        :param str database: Database name or filename for SQLite.
        :param kwargs: Arbitrary keyword arguments that will be passed to the
            database driver when a connection is created, for example
            ``password``, ``host``, etc.

        Initialize a *deferred* database.
        """
        if not self.is_closed():
            self.close()
        self.database = database
        self.connect_params.update(kwargs)
        self.deferred = not bool(database)

    def __enter__(self):
        """
        The :py:class:`Database` instance can be used as a context-manager, in
        which case a connection will be held open for the duration of the
        wrapped block.

        Additionally, any SQL executed within the wrapped block will be
        executed in a transaction.
        """
        if self.is_closed():
            self.connect()
        self.transaction().__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        top = self._state.transactions[-1]
        try:
            top.__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()

    def connection_context(self):
        """
        Create a context-manager that will hold open a connection for the
        duration of the wrapped block.

        Example::

            def on_app_startup():
                # When app starts up, create the database tables, being sure
                # the connection is closed upon completion.
                with database.connection_context():
                    database.create_tables(APP_MODELS)
        """
        return ConnectionContext(self)

    def _connect(self):
        raise NotImplementedError

    def connect(self, reuse_if_open=False):
        """
        :param bool reuse_if_open: Do not raise an exception if a connection is
            already opened.
        :returns: whether a new connection was opened.
        :rtype: bool
        :raises: ``OperationalError`` if connection already open and
            ``reuse_if_open`` is not set to ``True``.

        Open a connection to the database.
        """
        with self._lock:
            if self.deferred:
                raise Exception('Error, database must be initialized before '
                                'opening a connection.')
            if not self._state.closed:
                if reuse_if_open:
                    return False
                raise OperationalError('Connection already opened.')

            self._state.reset()
            with __exception_wrapper__:
                self._state.set_connection(self._connect())
                self._initialize_connection(self._state.conn)
        return True

    def _initialize_connection(self, conn):
        pass

    def close(self):
        """
        :returns: Whether a connection was closed. If the database was already
            closed, this returns ``False``.
        :rtype: bool

        Close the connection to the database.
        """
        with self._lock:
            if self.deferred:
                raise Exception('Error, database must be initialized before '
                                'opening a connection.')
            is_open = not self._state.closed
            try:
                if is_open:
                    with __exception_wrapper__:
                        self._close(self._state.conn)
            finally:
                self._state.reset()
            return is_open

    def _close(self, conn):
        conn.close()

    def is_closed(self):
        """
        :returns: return ``True`` if database is closed, ``False`` if open.
        :rtype: bool
        """
        return self._state.closed

    def connection(self):
        """
        Return the open connection. If a connection is not open, one will be
        opened. The connection will be whatever the underlying database-driver
        uses to encapsulate a database connection.
        """
        if self.is_closed():
            self.connect()
        return self._state.conn

    def cursor(self, commit=None):
        """
        Return a ``cursor`` object on the current connection. If a connection
        is not open, one will be opened. The cursor will be whatever the
        underlying database-driver uses to encapsulate a database cursor.
        """
        if self.is_closed():
            self.connect()
        return self._state.conn.cursor()

    def execute_sql(self, sql, params=None, commit=SENTINEL):
        """
        :param str sql: SQL string to execute.
        :param tuple params: Parameters for query.
        :param commit: Boolean flag to override the default commit logic.
        :returns: cursor object.

        Execute a SQL query and return a cursor over the results.
        """
        logger.debug((sql, params))
        if commit is SENTINEL:
            if self.in_transaction():
                commit = False
            elif self.commit_select:
                commit = True
            else:
                commit = not sql[:6].lower().startswith('select')

        with __exception_wrapper__:
            cursor = self.cursor(commit)
            try:
                cursor.execute(sql, params or ())
            except Exception:
                if self.autorollback and not self.in_transaction():
                    self.rollback()
                raise
            else:
                if commit and not self.in_transaction():
                    self.commit()
        return cursor

    def execute(self, query, commit=SENTINEL, **context_options):
        """
        :param query: A :py:class:`Query` instance.
        :param commit: Boolean flag to override the default commit logic.
        :param context_options: Arbitrary options to pass to the SQL generator.
        :returns: cursor object.

        Execute a SQL query by compiling a ``Query`` instance and executing the
        resulting SQL.
        """
        ctx = self.get_sql_context(**context_options)
        sql, params = ctx.sql(query).query()
        return self.execute_sql(sql, params, commit=commit)

    def get_context_options(self):
        return {
            'field_types': self._field_types,
            'operations': self._operations,
            'param': self.param,
            'quote': self.quote,
            'compound_select_parentheses': self.compound_select_parentheses,
            'conflict_statement': self.conflict_statement,
            'conflict_update': self.conflict_update,
            'for_update': self.for_update,
            'limit_max': self.limit_max,
        }

    def get_sql_context(self, **context_options):
        context = self.get_context_options()
        if context_options:
            context.update(context_options)
        return self.context_class(**context)

    def conflict_statement(self, on_conflict):
        raise NotImplementedError

    def conflict_update(self, on_conflict):
        raise NotImplementedError

    def last_insert_id(self, cursor, query_type=None):
        """
        :param cursor: cursor object.
        :returns: primary key of last-inserted row.
        """
        return cursor.lastrowid

    def rows_affected(self, cursor):
        """
        :param cursor: cursor object.
        :returns: number of rows modified by query.
        """
        return cursor.rowcount

    def default_values_insert(self, ctx):
        return ctx.literal('DEFAULT VALUES')

    def in_transaction(self):
        """
        :returns: whether or not a transaction is currently open.
        :rtype: bool
        """
        return bool(self._state.transactions)

    def push_transaction(self, transaction):
        self._state.transactions.append(transaction)

    def pop_transaction(self):
        return self._state.transactions.pop()

    def transaction_depth(self):
        return len(self._state.transactions)

    def top_transaction(self):
        if self._state.transactions:
            return self._state.transactions[-1]

    def atomic(self):
        """
        Create a context-manager which runs any queries in the wrapped block in
        a transaction (or save-point if blocks are nested).

        Calls to :py:meth:`~Database.atomic` can be nested.

        :py:meth:`~Database.atomic` can also be used as a decorator.

        Example code::

            with db.atomic() as txn:
                perform_operation()

                with db.atomic() as nested_txn:
                    perform_another_operation()

        Transactions and save-points can be explicitly committed or rolled-back
        within the wrapped block. If this occurs, a new transaction or
        savepoint is begun after the commit/rollback.

        Example::

            with db.atomic() as txn:
                User.create(username='mickey')
                txn.commit()  # Changes are saved and a new transaction begins.

                User.create(username='huey')
                txn.rollback()  # "huey" will not be saved.

                User.create(username='zaizee')

            # Print the usernames of all users.
            print [u.username for u in User.select()]

            # Prints ["mickey", "zaizee"]
        """
        return _atomic(self)

    def manual_commit(self):
        """
        Create a context-manager which disables all transaction management for
        the duration of the wrapped block.

        Example::

            with db.manual_commit():
                db.begin()  # Begin transaction explicitly.
                try:
                    user.delete_instance(recursive=True)
                except:
                    db.rollback()  # Rollback -- an error occurred.
                    raise
                else:
                    try:
                        db.commit()  # Attempt to commit changes.
                    except:
                        db.rollback()  # Error committing, rollback.
                        raise

        The above code is equivalent to the following::

            with db.atomic():
                user.delete_instance(recursive=True)
        """
        return _manual(self)

    def transaction(self):
        """
        Create a context-manager that runs all queries in the wrapped block in
        a transaction.

        .. warning::
            Calls to ``transaction`` cannot be nested. Only the top-most call
            will take effect. Rolling-back or committing a nested transaction
            context-manager has undefined behavior.
        """
        return _transaction(self)

    def savepoint(self):
        """
        Create a context-manager that runs all queries in the wrapped block in
        a savepoint. Savepoints can be nested arbitrarily.

        .. warning::
            Calls to ``savepoint`` must occur inside of a transaction.
        """
        return _savepoint(self)

    def begin(self):
        """
        Begin a transaction when using manual-commit mode.

        .. note::
            This method should only be used in conjunction with the
            :py:meth:`~Database.manual_commit` context manager.
        """
        pass

    def commit(self):
        """
        Manually commit the currently-active transaction.

        .. note::
            This method should only be used in conjunction with the
            :py:meth:`~Database.manual_commit` context manager.
        """
        return self._state.conn.commit()

    def rollback(self):
        """
        Manually roll-back the currently-active transaction.

        .. note::
            This method should only be used in conjunction with the
            :py:meth:`~Database.manual_commit` context manager.
        """
        return self._state.conn.rollback()

    def table_exists(self, table, schema=None):
        """
        :param str table: Table name.
        :param str schema: Schema name (optional).
        :returns: ``bool`` indicating whether table exists.
        """
        return table.__name__ in self.get_tables(schema=schema)

    def get_tables(self, schema=None):
        """
        :param str schema: Schema name (optional).
        :returns: a list of table names in the database.
        """
        raise NotImplementedError

    def get_indexes(self, table, schema=None):
        """
        :param str table: Table name.
        :param str schema: Schema name (optional).

        Return a list of :py:class:`IndexMetadata` tuples.

        Example::

            print db.get_indexes('entry')
            [IndexMetadata(
                 name='entry_public_list',
                 sql='CREATE INDEX "entry_public_list" ...',
                 columns=['timestamp'],
                 unique=False,
                 table='entry'),
             IndexMetadata(
                 name='entry_slug',
                 sql='CREATE UNIQUE INDEX "entry_slug" ON "entry" ("slug")',
                 columns=['slug'],
                 unique=True,
                 table='entry')]
        """
        raise NotImplementedError

    def get_columns(self, table, schema=None):
        """
        :param str table: Table name.
        :param str schema: Schema name (optional).

        Return a list of :py:class:`ColumnMetadata` tuples.

        Example::

            print db.get_columns('entry')
            [ColumnMetadata(
                 name='id',
                 data_type='INTEGER',
                 null=False,
                 primary_key=True,
                 table='entry'),
             ColumnMetadata(
                 name='title',
                 data_type='TEXT',
                 null=False,
                 primary_key=False,
                 table='entry'),
             ...]
        """
        raise NotImplementedError

    def get_primary_keys(self, table, schema=None):
        """
        :param str table: Table name.
        :param str schema: Schema name (optional).

        Return a list of column names that comprise the primary key.

        Example::

            print db.get_primary_keys('entry')
            ['id']
        """
        raise NotImplementedError

    def get_foreign_keys(self, table, schema=None):
        """
        :param str table: Table name.
        :param str schema: Schema name (optional).

        Return a list of :py:class:`ForeignKeyMetadata` tuples for keys present
        on the table.

        Example::

            print db.get_foreign_keys('entrytag')
            [ForeignKeyMetadata(
                 column='entry_id',
                 dest_table='entry',
                 dest_column='id',
                 table='entrytag'),
             ...]
        """
        raise NotImplementedError

    def sequence_exists(self, seq):
        """
        :param str seq: Name of sequence.
        :returns: Whether sequence exists.
        :rtype: bool
        """
        raise NotImplementedError

    def create_tables(self, models, **options):
        """
        :param list models: A list of :py:class:`Model` classes.
        :param options: Options to specify when calling
            :py:meth:`Model.create_table`.

        Create tables, indexes and associated metadata for the given list of
        models.

        Dependencies are resolved so that tables are created in the appropriate
        order.
        """
        for model in sort_models(models):
            model.create_table(**options)

    def drop_tables(self, models, **kwargs):
        """
        :param list models: A list of :py:class:`Model` classes.
        :param kwargs: Options to specify when calling
            :py:meth:`Model.drop_table`.

        Drop tables, indexes and associated metadata for the given list of
        models.

        Dependencies are resolved so that tables are dropped in the appropriate
        order.
        """
        for model in reversed(sort_models(models)):
            model.drop_table(**kwargs)

    def extract_date(self, date_part, date_field):
        raise NotImplementedError

    def truncate_date(self, date_part, date_field):
        raise NotImplementedError

    def bind(self, models, bind_refs=True, bind_backrefs=True):
        """
        :param list models: List of models to bind to the database.
        :param bool bind_refs: Bind models that are referenced using
            foreign-keys.
        :param bool bind_backrefs: Bind models that reference the given model
            with a foreign-key.

        Create a context-manager that binds (associates) the given models with
        the current database for the duration of the wrapped block.
        """
        return _BoundModelsContext(models, self, bind_refs, bind_backrefs)

    def get_noop_select(self, ctx):
        return ctx.sql(Select().columns(SQL('0')).where(SQL('0')))


def __pragma__(name):
    def __get__(self):
        return self.pragma(name)
    def __set__(self, value):
        return self.pragma(name, value)
    return property(__get__, __set__)


class SqliteDatabase(Database):
    """
    Sqlite database implementation.

    Additional optional keyword-parameters:

    :param list pragmas: A list of 2-tuples containing pragma key and value to
        set whenever a connection is opened.
    :param timeout: Set the busy-timeout on the SQLite driver (in seconds).

    Example of using PRAGMAs::

        db = SqliteDatabase('my_app.db', pragmas=(
            ('cache_size', -16000),  # 16MB
            ('journal_mode', 'wal'),  # Use write-ahead-log journal mode.
        ))
    """
    field_types = {
        'BIGINT': FIELD.INT,
        'BOOL': FIELD.INT,
        'DOUBLE': FIELD.FLOAT,
        'SMALLINT': FIELD.INT,
        'UUID': FIELD.TEXT}
    operations = {
        'LIKE': 'GLOB',
        'ILIKE': 'LIKE'}
    limit_max = -1

    def __init__(self, database, *args, **kwargs):
        super(SqliteDatabase, self).__init__(database, *args, **kwargs)
        self._aggregates = {}
        self._collations = {}
        self._functions = {}
        self.register_function(_sqlite_date_part, 'date_part', 2)
        self.register_function(_sqlite_date_trunc, 'date_trunc', 2)

    def init(self, database, pragmas=None, timeout=5, **kwargs):
        self._pragmas = pragmas or ()
        self._timeout = timeout
        super(SqliteDatabase, self).init(database, **kwargs)

    def _connect(self):
        conn = sqlite3.connect(self.database, timeout=self._timeout,
                               **self.connect_params)
        conn.isolation_level = None
        try:
            self._add_conn_hooks(conn)
        except:
            conn.close()
            raise
        return conn

    def _add_conn_hooks(self, conn):
        self._set_pragmas(conn)
        self._load_aggregates(conn)
        self._load_collations(conn)
        self._load_functions(conn)

    def _set_pragmas(self, conn):
        if self._pragmas:
            cursor = conn.cursor()
            for pragma, value in self._pragmas:
                cursor.execute('PRAGMA %s = %s;' % (pragma, value))
            cursor.close()

    def pragma(self, key, value=SENTINEL):
        """
        :param key: Setting name.
        :param value: New value for the setting (optional).

        Execute a PRAGMA query once on the active connection. If a value is not
        specified, then the current value will be returned.

        .. note::
            This only affects the current connection. If the PRAGMA being
            executed is not persistent, then it will only be in effect for the
            lifetime of the connection (or until over-written).
        """
        sql = 'PRAGMA %s' % key
        if value is not SENTINEL:
            sql += ' = %s' % (value or 0)
        row = self.execute_sql(sql).fetchone()
        if row:
            return row[0]

    #: Get or set the cache_size pragma.
    cache_size = __pragma__('cache_size')

    #: Get or set the foreign_keys pragma.
    foreign_keys = __pragma__('foreign_keys')

    #: Get or set the journal_mode pragma.
    journal_mode = __pragma__('journal_mode')

    #: Get or set the journal_size_limit pragma.
    journal_size_limit = __pragma__('journal_size_limit')

    #: Get or set the mmap_size pragma.
    mmap_size = __pragma__('mmap_size')

    #: Get or set the page_size pragma.
    page_size = __pragma__('page_size')

    #: Get or set the read_uncommitted pragma.
    read_uncommitted = __pragma__('read_uncommitted')

    #: Get or set the synchronous pragma.
    synchronous = __pragma__('synchronous')

    #: Get or set the wal_autocheckpoint pragma.
    wal_autocheckpoint = __pragma__('wal_autocheckpoint')

    @property
    def timeout(self):
        """
        Get or set the busy timeout (seconds).
        """
        return self._timeout

    @timeout.setter
    def timeout(self, seconds):
        if self._timeout == seconds:
            return

        self._timeout = seconds
        if not self.is_closed():
            self.execute_sql('PRAGMA busy_timeout=%d;' % (seconds * 1000))

    def _load_aggregates(self, conn):
        for name, (klass, num_params) in self._aggregates.items():
            conn.create_aggregate(name, num_params, klass)

    def _load_collations(self, conn):
        for name, fn in self._collations.items():
            conn.create_collation(name, fn)

    def _load_functions(self, conn):
        for name, (fn, num_params) in self._functions.items():
            conn.create_function(name, num_params, fn)

    def register_aggregate(self, klass, name=None, num_params=-1):
        """
        :param klass: Class implementing aggregate API.
        :param str name: Aggregate function name (defaults to name of class).
        :param int num_params: Number of parameters the aggregate accepts, or
            -1 for any number.

        Register a user-defined aggregate function.

        The function will be registered each time a new connection is opened.
        Additionally, if a connection is already open, the aggregate will be
        registered with the open connection.
        """
        self._aggregates[name or klass.__name__.lower()] = (klass, num_params)
        if not self.is_closed():
            self._load_aggregates(self.connection())

    def aggregate(self, name=None, num_params=-1):
        """
        :param str name: Name of the aggregate (defaults to class name).
        :param int num_params: Number of parameters the aggregate accepts,
            or -1 for any number.

        Decorator to register a user-defined aggregate function.

        Example::

            @db.aggregate('md5')
            class MD5(object):
                def initialize(self):
                    self.md5 = hashlib.md5()

                def step(self, value):
                    self.md5.update(value)

                def finalize(self):
                    return self.md5.hexdigest()


            @db.aggregate()
            class Product(object):
                '''Like SUM() except calculates cumulative product.'''
                def __init__(self):
                    self.product = 1

                def step(self, value):
                    self.product *= value

                def finalize(self):
                    return self.product
        """
        def decorator(klass):
            self.register_aggregate(klass, name, num_params)
            return klass
        return decorator

    def register_collation(self, fn, name=None):
        """
        :param fn: The collation function.
        :param str name: Name of collation (defaults to function name)

        Register a user-defined collation. The collation will be registered
        each time a new connection is opened.  Additionally, if a connection is
        already open, the collation will be registered with the open
        connection.
        """
        name = name or fn.__name__
        def _collation(*args):
            expressions = args + (SQL('collate %s' % name),)
            return NodeList(expressions)
        fn.collation = _collation
        self._collations[name] = fn
        if not self.is_closed():
            self._load_collations(self.connection())

    def collation(self, name=None):
        """
        :param str name: Name of collation (defaults to function name)

        Decorator to register a user-defined collation.

        Example::

            @db.collation('reverse')
            def collate_reverse(s1, s2):
                return -cmp(s1, s2)

            # Usage:
            Book.select().order_by(collate_reverse.collation(Book.title))
        """
        def decorator(fn):
            self.register_collation(fn, name)
            return fn
        return decorator

    def register_function(self, fn, name=None, num_params=-1):
        """
        :param fn: The user-defined scalar function.
        :param str name: Name of function (defaults to function name)
        :param int num_params: Number of arguments the function accepts, or
            -1 for any number.

        Register a user-defined scalar function. The function will be
        registered each time a new connection is opened.  Additionally, if a
        connection is already open, the function will be registered with the
        open connection.
        """
        self._functions[name or fn.__name__] = (fn, num_params)
        if not self.is_closed():
            self._load_functions(self.connection())

    def func(self, name=None, num_params=-1):
        """
        :param str name: Name of the function (defaults to function name).
        :param int num_params: Number of parameters the function accepts,
            or -1 for any number.

        Decorator to register a user-defined scalar function.

        Example::

            @db.func('title_case')
            def title_case(s):
                return s.title() if s else ''

            # Usage:
            title_case_books = Book.select(fn.title_case(Book.title))
        """
        def decorator(fn):
            self.register_function(fn, name, num_params)
            return fn
        return decorator

    def unregister_aggregate(self, name):
        """
        :param name: Name of the user-defined aggregate function.

        Unregister the user-defined aggregate function.
        """
        del(self._aggregates[name])

    def unregister_collation(self, name):
        """
        :param name: Name of the user-defined collation.

        Unregister the user-defined collation.
        """
        del(self._collations[name])

    def unregister_function(self, name):
        """
        :param name: Name of the user-defined scalar function.

        Unregister the user-defined scalar function.
        """
        del(self._functions[name])

    def transaction(self, lock_type=None):
        """
        :param str lock_type: Locking strategy: DEFERRED, IMMEDIATE, EXCLUSIVE.

        Create a transaction context-manager using the specified locking
        strategy (defaults to DEFERRED).
        """
        return _transaction(self, lock_type=lock_type)

    def begin(self, lock_type=None):
        statement = 'BEGIN %s' % lock_type if lock_type else 'BEGIN'
        self.execute_sql(statement, commit=False)

    def get_tables(self, schema=None):
        cursor = self.execute_sql('SELECT name FROM sqlite_master WHERE '
                                  'type = ? ORDER BY name;', ('table',))
        return [row for row, in cursor.fetchall()]

    def get_indexes(self, table, schema=None):
        query = ('SELECT name, sql FROM sqlite_master '
                 'WHERE tbl_name = ? AND type = ? ORDER BY name')
        cursor = self.execute_sql(query, (table, 'index'))
        index_to_sql = dict(cursor.fetchall())

        # Determine which indexes have a unique constraint.
        unique_indexes = set()
        cursor = self.execute_sql('PRAGMA index_list("%s")' % table)
        for row in cursor.fetchall():
            name = row[1]
            is_unique = int(row[2]) == 1
            if is_unique:
                unique_indexes.add(name)

        # Retrieve the indexed columns.
        index_columns = {}
        for index_name in sorted(index_to_sql):
            cursor = self.execute_sql('PRAGMA index_info("%s")' % index_name)
            index_columns[index_name] = [row[2] for row in cursor.fetchall()]

        return [
            IndexMetadata(
                name,
                index_to_sql[name],
                index_columns[name],
                name in unique_indexes,
                table)
            for name in sorted(index_to_sql)]

    def get_columns(self, table, schema=None):
        cursor = self.execute_sql('PRAGMA table_info("%s")' % table)
        return [ColumnMetadata(row[1], row[2], not row[3], bool(row[5]), table)
                for row in cursor.fetchall()]

    def get_primary_keys(self, table, schema=None):
        cursor = self.execute_sql('PRAGMA table_info("%s")' % table)
        return [row[1] for row in filter(lambda r: r[-1], cursor.fetchall())]

    def get_foreign_keys(self, table, schema=None):
        cursor = self.execute_sql('PRAGMA foreign_key_list("%s")' % table)
        return [ForeignKeyMetadata(row[3], row[2], row[4], table)
                for row in cursor.fetchall()]

    def get_binary_type(self):
        return sqlite3.Binary

    def conflict_statement(self, on_conflict):
        if on_conflict._action:
            return SQL('INSERT OR %s' % on_conflict._action.upper())

    def conflict_update(self, on_conflict):
        if any((on_conflict._preserve, on_conflict._update, on_conflict._where,
                on_conflict._conflict_target)):
            raise ValueError('SQLite does not support specifying which values '
                             'to preserve or update.')

    def extract_date(self, date_part, date_field):
        return fn.date_part(date_part, date_field)

    def truncate_date(self, date_part, date_field):
        return fn.strftime(SQLITE_DATE_TRUNC_MAPPING[date_part], date_field)


class PostgresqlDatabase(Database):
    """
    Postgresql database implementation.

    Additional optional keyword-parameters:

    :param bool register_unicode: Register unicode types.
    :param str encoding: Database encoding.
    """
    field_types = {
        'AUTO': 'SERIAL',
        'BLOB': 'BYTEA',
        'BOOL': 'BOOLEAN',
        'DATETIME': 'TIMESTAMP',
        'DECIMAL': 'NUMERIC',
        'DOUBLE': 'DOUBLE PRECISION',
        'UUID': 'UUID'}
    operations = {'REGEXP': '~'}
    param = '%s'

    commit_select = True
    compound_select_parentheses = True
    for_update = True
    returning_clause = True
    safe_create_index = False
    sequences = True

    def init(self, database, register_unicode=True, encoding=None, **kwargs):
        self._register_unicode = register_unicode
        self._encoding = encoding
        self._need_server_version = True
        super(PostgresqlDatabase, self).init(database, **kwargs)

    def _connect(self):
        conn = psycopg2.connect(database=self.database, **self.connect_params)
        if self._register_unicode:
            pg_extensions.register_type(pg_extensions.UNICODE, conn)
            pg_extensions.register_type(pg_extensions.UNICODEARRAY, conn)
        if self._encoding:
            conn.set_client_encoding(self._encoding)
        if self._need_server_version:
            self.set_server_version(conn.server_version)
            self._need_server_version = False
        return conn

    def set_server_version(self, version):
        if version >= 90600:
            self.safe_create_index = True

    def last_insert_id(self, cursor, query_type=None):
        try:
            return cursor if query_type else cursor[0][0]
        except (IndexError, TypeError):
            pass

    def get_tables(self, schema=None):
        query = ('SELECT tablename FROM pg_catalog.pg_tables '
                 'WHERE schemaname = %s ORDER BY tablename')
        cursor = self.execute_sql(query, (schema or 'public',))
        return [table for table, in cursor.fetchall()]

    def get_indexes(self, table, schema=None):
        query = """
            SELECT
                i.relname, idxs.indexdef, idx.indisunique,
                array_to_string(array_agg(cols.attname), ',')
            FROM pg_catalog.pg_class AS t
            INNER JOIN pg_catalog.pg_index AS idx ON t.oid = idx.indrelid
            INNER JOIN pg_catalog.pg_class AS i ON idx.indexrelid = i.oid
            INNER JOIN pg_catalog.pg_indexes AS idxs ON
                (idxs.tablename = t.relname AND idxs.indexname = i.relname)
            LEFT OUTER JOIN pg_catalog.pg_attribute AS cols ON
                (cols.attrelid = t.oid AND cols.attnum = ANY(idx.indkey))
            WHERE t.relname = %s AND t.relkind = %s AND idxs.schemaname = %s
            GROUP BY i.relname, idxs.indexdef, idx.indisunique
            ORDER BY idx.indisunique DESC, i.relname;"""
        cursor = self.execute_sql(query, (table, 'r', schema or 'public'))
        return [IndexMetadata(row[0], row[1], row[3].split(','), row[2], table)
                for row in cursor.fetchall()]

    def get_columns(self, table, schema=None):
        query = """
            SELECT column_name, is_nullable, data_type
            FROM information_schema.columns
            WHERE table_name = %s AND table_schema = %s
            ORDER BY ordinal_position"""
        cursor = self.execute_sql(query, (table, schema or 'public'))
        pks = set(self.get_primary_keys(table, schema))
        return [ColumnMetadata(name, dt, null == 'YES', name in pks, table)
                for name, null, dt in cursor.fetchall()]

    def get_primary_keys(self, table, schema=None):
        query = """
            SELECT kc.column_name
            FROM information_schema.table_constraints AS tc
            INNER JOIN information_schema.key_column_usage AS kc ON (
                tc.table_name = kc.table_name AND
                tc.table_schema = kc.table_schema AND
                tc.constraint_name = kc.constraint_name)
            WHERE
                tc.constraint_type = %s AND
                tc.table_name = %s AND
                tc.table_schema = %s"""
        ctype = 'PRIMARY KEY'
        cursor = self.execute_sql(query, (ctype, table, schema or 'public'))
        return [pk for pk, in cursor.fetchall()]

    def get_foreign_keys(self, table, schema=None):
        sql = """
            SELECT
                kcu.column_name, ccu.table_name, ccu.column_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
                ON (tc.constraint_name = kcu.constraint_name AND
                    tc.constraint_schema = kcu.constraint_schema)
            JOIN information_schema.constraint_column_usage AS ccu
                ON (ccu.constraint_name = tc.constraint_name AND
                    ccu.constraint_schema = tc.constraint_schema)
            WHERE
                tc.constraint_type = 'FOREIGN KEY' AND
                tc.table_name = %s AND
                tc.table_schema = %s"""
        cursor = self.execute_sql(sql, (table, schema or 'public'))
        return [ForeignKeyMetadata(row[0], row[1], row[2], table)
                for row in cursor.fetchall()]

    def sequence_exists(self, sequence):
        res = self.execute_sql("""
            SELECT COUNT(*) FROM pg_class, pg_namespace
            WHERE relkind='S'
                AND pg_class.relnamespace = pg_namespace.oid
                AND relname=%s""", (sequence,))
        return bool(res.fetchone()[0])

    def get_binary_type(self):
        return psycopg2.Binary

    def conflict_statement(self, on_conflict):
        return

    def conflict_update(self, on_conflict):
        action = on_conflict._action.lower() if on_conflict._action else ''
        if action in ('ignore', 'nothing'):
            return SQL('ON CONFLICT DO NOTHING')
        elif action and action != 'update':
            raise ValueError('The only supported actions for conflict '
                             'resolution with Postgresql are "ignore" or '
                             '"update".')
        elif not on_conflict._update and not on_conflict._preserve:
            raise ValueError('If you are not performing any updates (or '
                             'preserving any INSERTed values), then the '
                             'conflict resolution action should be set to '
                             '"IGNORE".')
        elif not on_conflict._conflict_target:
            raise ValueError('Postgres requires that a conflict target be '
                             'specified when doing an upsert.')

        target = EnclosedNodeList([
            Entity(col) if isinstance(col, basestring) else col
            for col in on_conflict._conflict_target])

        updates = []
        if on_conflict._preserve:
            for column in on_conflict._preserve:
                excluded = NodeList((SQL('EXCLUDED'), ensure_entity(column)),
                                    glue='.')
                expression = NodeList((ensure_entity(column), SQL('='),
                                       excluded))
                updates.append(expression)

        if on_conflict._update:
            for k, v in on_conflict._update.items():
                if not isinstance(v, Node):
                    converter = k.db_value if isinstance(k, Field) else None
                    v = Value(v, converter=converter, unpack=False)
                updates.append(NodeList((ensure_entity(k), SQL('='), v)))

        parts = [SQL('ON CONFLICT'),
                 target,
                 SQL('DO UPDATE SET'),
                 CommaNodeList(updates)]
        if on_conflict._where:
            parts.extend((SQL('WHERE'), on_conflict._where))

        return NodeList(parts)

    def extract_date(self, date_part, date_field):
        return fn.EXTRACT(NodeList((date_part, SQL('FROM'), date_field)))

    def truncate_date(self, date_part, date_field):
        return fn.DATE_TRUNC(date_part, date_field)

    def get_noop_select(self, ctx):
        return ctx.sql(Select().columns(SQL('0')).where(SQL('false')))


class MySQLDatabase(Database):
    """
    MySQL database implementation.
    """
    field_types = {
        'AUTO': 'INTEGER AUTO_INCREMENT',
        'BOOL': 'BOOL',
        'DECIMAL': 'NUMERIC',
        'DOUBLE': 'DOUBLE PRECISION',
        'FLOAT': 'FLOAT',
        'UUID': 'VARCHAR(40)'}
    operations = {
        'LIKE': 'LIKE BINARY',
        'ILIKE': 'LIKE',
        'XOR': 'XOR'}
    param = '%s'
    quote = '`'

    commit_select = True
    for_update = True
    limit_max = 2 ** 64 - 1
    safe_create_index = False
    safe_drop_index = False

    def init(self, database, **kwargs):
        params = {'charset': 'utf8', 'use_unicode': True}
        params.update(kwargs)
        if 'password' in params:
            params['passwd'] = params.pop('password')
        super(MySQLDatabase, self).init(database, **params)

    def _connect(self):
        return mysql.connect(db=self.database, **self.connect_params)

    def default_values_insert(self, ctx):
        return ctx.literal('() VALUES ()')

    def get_tables(self, schema=None):
        return [table for table, in self.execute_sql('SHOW TABLES')]

    def get_indexes(self, table, schema=None):
        cursor = self.execute_sql('SHOW INDEX FROM `%s`' % table)
        unique = set()
        indexes = {}
        for row in cursor.fetchall():
            if not row[1]:
                unique.add(row[2])
            indexes.setdefault(row[2], [])
            indexes[row[2]].append(row[4])
        return [IndexMetadata(name, None, indexes[name], name in unique, table)
                for name in indexes]

    def get_columns(self, table, schema=None):
        sql = """
            SELECT column_name, is_nullable, data_type
            FROM information_schema.columns
            WHERE table_name = %s AND table_schema = DATABASE()"""
        cursor = self.execute_sql(sql, (table,))
        pks = set(self.get_primary_keys(table))
        return [ColumnMetadata(name, dt, null == 'YES', name in pks, table)
                for name, null, dt in cursor.fetchall()]

    def get_primary_keys(self, table, schema=None):
        cursor = self.execute_sql('SHOW INDEX FROM `%s`' % table)
        return [row[4] for row in
                filter(lambda row: row[2] == 'PRIMARY', cursor.fetchall())]

    def get_foreign_keys(self, table, schema=None):
        query = """
            SELECT column_name, referenced_table_name, referenced_column_name
            FROM information_schema.key_column_usage
            WHERE table_name = %s
                AND table_schema = DATABASE()
                AND referenced_table_name IS NOT NULL
                AND referenced_column_name IS NOT NULL"""
        cursor = self.execute_sql(query, (table,))
        return [
            ForeignKeyMetadata(column, dest_table, dest_column, table)
            for column, dest_table, dest_column in cursor.fetchall()]

    def get_binary_type(self):
        return mysql.Binary

    def conflict_statement(self, on_conflict):
        if not on_conflict._action: return

        action = on_conflict._action.lower()
        if action == 'replace':
            return SQL('REPLACE')
        elif action == 'ignore':
            return SQL('INSERT IGNORE')
        elif action != 'update':
            raise ValueError('Un-supported action for conflict resolution. '
                             'MySQL supports REPLACE, IGNORE and UPDATE.')

    def conflict_update(self, on_conflict):
        if on_conflict._where or on_conflict._conflict_target:
            raise ValueError('MySQL does not support the specification of '
                             'where clauses or conflict targets for conflict '
                             'resolution.')

        updates = []
        if on_conflict._preserve:
            for column in on_conflict._preserve:
                entity = ensure_entity(column)
                expression = NodeList((
                    ensure_entity(column),
                    SQL('='),
                    fn.VALUES(entity)))
                updates.append(expression)

        if on_conflict._update:
            for k, v in on_conflict._update.items():
                if not isinstance(v, Node):
                    converter = k.db_value if isinstance(k, Field) else None
                    v = Value(v, converter=converter, unpack=False)
                updates.append(NodeList((ensure_entity(k), SQL('='), v)))

        if updates:
            return NodeList((SQL('ON DUPLICATE KEY UPDATE'),
                             CommaNodeList(updates)))

    def extract_date(self, date_part, date_field):
        return fn.EXTRACT(NodeList((SQL(date_part), SQL('FROM'), date_field)))

    def truncate_date(self, date_part, date_field):
        return fn.DATE_FORMAT(date_field, __mysql_date_trunc__[date_part])

    def get_noop_select(self, ctx):
        return ctx.literal('DO 0')


# TRANSACTION CONTROL.


class _manual(_callable_context_manager):
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        top = self.db.top_transaction()
        if top and not isinstance(self.db.top_transaction(), _manual):
            raise ValueError('Cannot enter manual commit block while a '
                             'transaction is active.')
        self.db.push_transaction(self)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.db.pop_transaction() is not self:
            raise ValueError('Transaction stack corrupted while exiting '
                             'manual commit block.')


class _atomic(_callable_context_manager):
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        if self.db.transaction_depth() == 0:
            self._helper = self.db.transaction()
        else:
            self._helper = self.db.savepoint()
            if isinstance(self.db.top_transaction(), _manual):
                raise ValueError('Cannot enter atomic commit block while in '
                                 'manual commit mode.')
        return self._helper.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._helper.__exit__(exc_type, exc_val, exc_tb)


class _transaction(_callable_context_manager):
    def __init__(self, db, lock_type=None):
        self.db = db
        self._lock_type = lock_type

    def _begin(self):
        if self._lock_type:
            self.db.begin(self._lock_type)
        else:
            self.db.begin()

    def commit(self, begin=True):
        self.db.commit()
        if begin:
            self._begin()

    def rollback(self, begin=True):
        self.db.rollback()
        if begin:
            self._begin()

    def __enter__(self):
        if self.db.transaction_depth() == 0:
            self._begin()
        self.db.push_transaction(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type:
                self.rollback(False)
            elif self.db.transaction_depth() == 1:
                try:
                    self.commit(False)
                except:
                    self.rollback(False)
                    raise
        finally:
            self.db.pop_transaction()


class _savepoint(_callable_context_manager):
    def __init__(self, db, sid=None):
        self.db = db
        self.sid = sid or 's' + uuid.uuid4().hex
        self.quoted_sid = self.sid.join((self.db.quote, self.db.quote))

    def _begin(self):
        self.db.execute_sql('SAVEPOINT %s;' % self.quoted_sid)

    def commit(self, begin=True):
        self.db.execute_sql('RELEASE SAVEPOINT %s;' % self.quoted_sid)
        if begin: self._begin()

    def rollback(self):
        self.db.execute_sql('ROLLBACK TO SAVEPOINT %s;' % self.quoted_sid)

    def __enter__(self):
        self._begin()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            try:
                self.commit(begin=False)
            except:
                self.rollback()
                raise


# CURSOR REPRESENTATIONS.


class CursorWrapper(object):
    def __init__(self, cursor):
        self.cursor = cursor
        self.count = 0
        self.index = 0
        self.initialized = False
        self.populated = False
        self.row_cache = []

    def __iter__(self):
        if self.populated:
            return iter(self.row_cache)
        return ResultIterator(self)

    def __getitem__(self, item):
        if isinstance(item, slice):
            # TODO: getslice
            start = item.start
            stop = item.stop
            if stop is None or stop < 0:
                self.fill_cache()
            else:
                self.fill_cache(stop)
            return self.row_cache[item]
        elif isinstance(item, int):
            self.fill_cache(item if item > 0 else 0)
            return self.row_cache[item]
        else:
            raise ValueError('CursorWrapper only supports integer and slice '
                             'indexes.')

    def __len__(self):
        self.fill_cache()
        return self.count

    def initialize(self):
        pass

    def iterate(self, cache=True):
        row = self.cursor.fetchone()
        if row is None:
            self.populated = True
            self.cursor.close()
            raise StopIteration
        elif not self.initialized:
            self.initialize()  # Lazy initialization.
            self.initialized = True
        self.count += 1
        result = self.process_row(row)
        if cache:
            self.row_cache.append(result)
        return result

    def process_row(self, row):
        return row

    def iterator(self):
        """Efficient one-pass iteration over the result set."""
        while True:
            yield self.iterate(False)

    def fill_cache(self, n=0.):
        n = n or float('Inf')
        if n < 0:
            raise ValueError('Negative values are not supported.')

        iterator = ResultIterator(self)
        iterator.index = self.count
        while not self.populated and (n > self.count):
            try:
                iterator.next()
            except StopIteration:
                break


class DictCursorWrapper(CursorWrapper):
    def _initialize_columns(self):
        description = self.cursor.description
        self.columns = [t[0][t[0].find('.') + 1:]
                        for t in description]
        self.ncols = len(description)

    initialize = _initialize_columns

    def _row_to_dict(self, row):
        result = {}
        for i in range(self.ncols):
            result[self.columns[i]] = row[i]
        return result

    process_row = _row_to_dict


class NamedTupleCursorWrapper(CursorWrapper):
    def initialize(self):
        description = self.cursor.description
        self.tuple_class = namedtuple(
            'Row',
            [col[0][col[0].find('.') + 1:].strip('"') for col in description])

    def process_row(self, row):
        return self.tuple_class(*row)


class ObjectCursorWrapper(DictCursorWrapper):
    def __init__(self, cursor, constructor):
        super(ObjectCursorWrapper, self).__init__(cursor)
        self.constructor = constructor

    def process_row(self, row):
        row_dict = self._row_to_dict(row)
        return self.constructor(**row_dict)


class ResultIterator(object):
    def __init__(self, cursor_wrapper):
        self.cursor_wrapper = cursor_wrapper
        self.index = 0

    def __iter__(self):
        return self

    def next(self):
        if self.index < self.cursor_wrapper.count:
            obj = self.cursor_wrapper.row_cache[self.index]
        elif not self.cursor_wrapper.populated:
            self.cursor_wrapper.iterate()
            obj = self.cursor_wrapper.row_cache[self.index]
        else:
            raise StopIteration
        self.index += 1
        return obj

    __next__ = next


# FIELDS


class FieldAccessor(object):
    def __init__(self, model, field, name):
        self.model = model
        self.field = field
        self.name = name

    def __get__(self, instance, instance_type=None):
        if instance is not None:
            return instance.__data__.get(self.name)
        return self.field

    def __set__(self, instance, value):
        instance.__data__[self.name] = value
        instance._dirty.add(self.name)


class ForeignKeyAccessor(FieldAccessor):
    def __init__(self, model, field, name):
        super(ForeignKeyAccessor, self).__init__(model, field, name)
        self.rel_model = field.rel_model

    def get_rel_instance(self, instance):
        value = instance.__data__.get(self.name)
        if value is not None or self.name in instance.__rel__:
            if self.name not in instance.__rel__:
                obj = self.rel_model.get(self.field.rel_field == value)
                instance.__rel__[self.name] = obj
            return instance.__rel__[self.name]
        elif not self.field.null:
            raise self.rel_model.DoesNotExist
        return value

    def __get__(self, instance, instance_type=None):
        if instance is not None:
            return self.get_rel_instance(instance)
        return self.field

    def __set__(self, instance, obj):
        if isinstance(obj, self.rel_model):
            instance.__data__[self.name] = getattr(obj, self.field.rel_field.name)
            instance.__rel__[self.name] = obj
        else:
            fk_value = instance.__data__.get(self.name)
            instance.__data__[self.name] = obj
            if obj != fk_value and self.name in instance.__rel__:
                del instance.__rel__[self.name]


class BackrefAccessor(object):
    def __init__(self, field):
        self.field = field
        self.model = field.model

    def __get__(self, instance, instance_type=None):
        if instance is not None:
            dest = self.field.rel_field.name
            return (self.model
                    .select()
                    .where(self.field == getattr(instance, dest)))
        return self


class ObjectIdAccessor(object):
    """Gives direct access to the underlying id"""
    def __init__(self, field):
        self.field = field

    def __get__(self, instance, instance_type=None):
        if instance is not None:
            return instance.__data__.get(self.field.name)
        return self.field

    def __set__(self, instance, value):
        setattr(instance, self.field.name, value)



class Field(ColumnBase):
    _field_counter = 0
    _order = 0
    accessor_class = FieldAccessor
    auto_increment = False
    field_type = 'DEFAULT'

    def __init__(self, null=False, index=False, unique=False, column_name=None,
                 default=None, primary_key=False, constraints=None,
                 sequence=None, collation=None, unindexed=False, choices=None,
                 help_text=None, verbose_name=None, db_column=None,
                 _hidden=False):
        if db_column is not None:
            __deprecated__('"db_column" has been deprecated in favor of '
                           '"column_name" for Field objects.')
            column_name = db_column

        self.null = null
        self.index = index
        self.unique = unique
        self.column_name = column_name
        self.default = default
        self.primary_key = primary_key
        self.constraints = constraints  # List of column constraints.
        self.sequence = sequence  # Name of sequence, e.g. foo_id_seq.
        self.collation = collation
        self.unindexed = unindexed
        self.choices = choices
        self.help_text = help_text
        self.verbose_name = verbose_name
        self._hidden = _hidden

        # Used internally for recovering the order in which Fields were defined
        # on the Model class.
        Field._field_counter += 1
        self._order = Field._field_counter
        self._sort_key = (self.primary_key and 1 or 2), self._order

    def __hash__(self):
        return hash(self.name + '.' + self.model.__name__)

    def bind(self, model, name, set_attribute=True):
        self.model = model
        self.name = name
        self.column_name = self.column_name or name
        if set_attribute:
            setattr(model, name, self.accessor_class(model, self, name))

    @property
    def column(self):
        return Column(self.model._meta.table, self.column_name)

    def coerce(self, value):
        return value

    def db_value(self, value):
        return value if value is None else self.coerce(value)

    def python_value(self, value):
        return value if value is None else self.coerce(value)

    def get_sort_key(self, ctx):
        return self._sort_key

    def __sql__(self, ctx):
        return ctx.sql(self.column)

    def get_modifiers(self):
        return

    def ddl_datatype(self, ctx):
        if ctx and ctx.state.field_types:
            column_type = ctx.state.field_types.get(self.field_type,
                                                    self.field_type)
        else:
            column_type = self.field_type

        modifiers = self.get_modifiers()
        if column_type and modifiers:
            modifier_literal = ', '.join([str(m) for m in modifiers])
            return SQL('%s(%s)' % (column_type, modifier_literal))
        else:
            return SQL(column_type)

    def ddl(self, ctx):
        accum = [Entity(self.column_name)]
        data_type = self.ddl_datatype(ctx)
        if data_type:
            accum.append(data_type)
        if self.unindexed:
            accum.append(SQL('UNINDEXED'))
        if not self.null:
            accum.append(SQL('NOT NULL'))
        if self.primary_key:
            accum.append(SQL('PRIMARY KEY'))
        if self.sequence:
            accum.append(SQL("DEFAULT NEXTVAL('%s')" % self.sequence))
        if self.constraints:
            accum.extend(self.constraints)
        if self.collation:
            accum.append(SQL('COLLATE %s' % self.collation))
        return NodeList(accum)


class IntegerField(Field):
    field_type = 'INT'
    coerce = int


class BigIntegerField(IntegerField):
    field_type = 'BIGINT'


class SmallIntegerField(IntegerField):
    field_type = 'SMALLINT'


class AutoField(IntegerField):
    auto_increment = True
    field_type = 'AUTO'

    def __init__(self, *args, **kwargs):
        if kwargs.get('primary_key') is False:
            raise ValueError('AutoField must always be a primary key.')
        kwargs['primary_key'] = True
        super(AutoField, self).__init__(*args, **kwargs)


class FloatField(Field):
    field_type = 'FLOAT'
    coerce = float


class DoubleField(FloatField):
    field_type = 'DOUBLE'


class DecimalField(Field):
    field_type = 'DECIMAL'

    def __init__(self, max_digits=10, decimal_places=5, auto_round=False,
                 rounding=None, *args, **kwargs):
        self.max_digits = max_digits
        self.decimal_places = decimal_places
        self.auto_round = auto_round
        self.rounding = rounding or decimal.DefaultContext.rounding
        super(DecimalField, self).__init__(*args, **kwargs)

    def get_modifiers(self):
        return [self.max_digits, self.decimal_places]

    def db_value(self, value):
        D = decimal.Decimal
        if not value:
            return value if value is None else D(0)
        if self.auto_round:
            exp = D(10) ** (-self.decimal_places)
            rounding = self.rounding
            return D(str(value)).quantize(exp, rounding=rounding)
        return value

    def python_value(self, value):
        if value is not None:
            if isinstance(value, decimal.Decimal):
                return value
            return decimal.Decimal(str(value))


class _StringField(Field):
    def coerce(self, value):
        if isinstance(value, text_type):
            return value
        elif isinstance(value, bytes_type):
            return value.decode('utf-8')
        return text_type(value)

    def __add__(self, other): return self.concat(other)
    def __radd__(self, other): return other.concat(self)


class CharField(_StringField):
    field_type = 'VARCHAR'

    def __init__(self, max_length=255, *args, **kwargs):
        self.max_length = max_length
        super(CharField, self).__init__(*args, **kwargs)

    def get_modifiers(self):
        return self.max_length and [self.max_length] or None


class FixedCharField(CharField):
    field_type = 'CHAR'

    def python_value(self, value):
        value = super(FixedCharField, self).python_value(value)
        if value:
            value = value.strip()
        return value


class TextField(_StringField):
    field_type = 'TEXT'


class BlobField(Field):
    field_type = 'BLOB'

    def bind(self, model, name, set_attribute=True):
        self._constructor = model._meta.database.get_binary_type()
        return super(BlobField, self).bind(model, name, set_attribute)

    def db_value(self, value):
        if isinstance(value, text_type):
            value = value.encode('raw_unicode_escape')
        if isinstance(value, bytes_type):
            return self._constructor(value)
        return value


class UUIDField(Field):
    field_type = 'UUID'

    def db_value(self, value):
        if isinstance(value, uuid.UUID):
            return value.hex
        try:
            return uuid.UUID(value).hex
        except:
            return value

    def python_value(self, value):
        if isinstance(value, uuid.UUID):
            return value
        return None if value is None else uuid.UUID(value)


def _date_part(date_part):
    def dec(self):
        return self.model._meta.database.extract_date(date_part, self)
    return dec

def format_date_time(value, formats, post_process=None):
    post_process = post_process or (lambda x: x)
    for fmt in formats:
        try:
            return post_process(datetime.datetime.strptime(value, fmt))
        except ValueError:
            pass
    return value


class _BaseFormattedField(Field):
    formats = None

    def __init__(self, formats=None, *args, **kwargs):
        if formats is not None:
            self.formats = formats
        super(_BaseFormattedField, self).__init__(*args, **kwargs)


class DateTimeField(_BaseFormattedField):
    field_type = 'DATETIME'
    formats = [
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
    ]

    def python_value(self, value):
        if value and isinstance(value, basestring):
            return format_date_time(value, self.formats)
        return value

    year = property(_date_part('year'))
    month = property(_date_part('month'))
    day = property(_date_part('day'))
    hour = property(_date_part('hour'))
    minute = property(_date_part('minute'))
    second = property(_date_part('second'))


class DateField(_BaseFormattedField):
    field_type = 'DATE'
    formats = [
        '%Y-%m-%d',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M:%S.%f',
    ]

    def python_value(self, value):
        if value and isinstance(value, basestring):
            pp = lambda x: x.date()
            return format_date_time(value, self.formats, pp)
        elif value and isinstance(value, datetime.datetime):
            return value.date()
        return value

    year = property(_date_part('year'))
    month = property(_date_part('month'))
    day = property(_date_part('day'))


class TimeField(_BaseFormattedField):
    field_type = 'TIME'
    formats = [
        '%H:%M:%S.%f',
        '%H:%M:%S',
        '%H:%M',
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S',
    ]

    def python_value(self, value):
        if value:
            if isinstance(value, basestring):
                pp = lambda x: x.time()
                return format_date_time(value, self.formats, pp)
            elif isinstance(value, datetime.datetime):
                return value.time()
        if value is not None and isinstance(value, datetime.timedelta):
            return (datetime.datetime.min + value).time()
        return value

    hour = property(_date_part('hour'))
    minute = property(_date_part('minute'))
    second = property(_date_part('second'))


class TimestampField(IntegerField):
    # Support second -> microsecond resolution.
    valid_resolutions = [10**i for i in range(7)]

    def __init__(self, *args, **kwargs):
        self.resolution = kwargs.pop('resolution', 1) or 1
        if self.resolution not in self.valid_resolutions:
            raise ValueError('TimestampField resolution must be one of: %s' %
                             ', '.join(str(i) for i in self.valid_resolutions))

        self.utc = kwargs.pop('utc', False) or False
        _dt = datetime.datetime
        self._conv = _dt.utcfromtimestamp if self.utc else _dt.fromtimestamp
        _default = _dt.utcnow if self.utc else _dt.now
        kwargs.setdefault('default', _default)
        super(TimestampField, self).__init__(*args, **kwargs)

    def db_value(self, value):
        if value is None:
            return

        if isinstance(value, datetime.datetime):
            pass
        elif isinstance(value, datetime.date):
            value = datetime.datetime(value.year, value.month, value.day)
        else:
            return int(round(value * self.resolution))

        if self.utc:
            timestamp = calendar.timegm(value.utctimetuple())
        else:
            timestamp = time.mktime(value.timetuple())
        timestamp += (value.microsecond * .000001)
        if self.resolution > 1:
            timestamp *= self.resolution
        return int(round(timestamp))

    def python_value(self, value):
        if value is not None and isinstance(value, (int, float, long)):
            if value == 0:
                return
            elif self.resolution > 1:
                ticks_to_microsecond = 1000000 // self.resolution
                value, ticks = divmod(value, self.resolution)
                microseconds = ticks * ticks_to_microsecond
                return self._conv(value).replace(microsecond=microseconds)
            else:
                return self._conv(value)
        return value


class IPField(BigIntegerField):
    def db_value(self, val):
        if val is not None:
            return struct.unpack('!I', socket.inet_aton(val))[0]

    def python_value(self, val):
        if val is not None:
            return socket.inet_ntoa(struct.pack('!I', val))


class BooleanField(Field):
    field_type = 'BOOL'
    coerce = bool


class BareField(Field):
    def db_value(self, value): return value
    def python_value(self, value): return value

    def ddl(self, ctx):
        return Entity(self.column_name)


class ForeignKeyField(Field):
    accessor_class = ForeignKeyAccessor

    def __init__(self, model, field=None, backref=None, on_delete=None,
                 on_update=None, _deferred=None, rel_model=None, to_field=None,
                 object_id_name=None, related_name=None, *args, **kwargs):
        super(ForeignKeyField, self).__init__(*args, **kwargs)
        if rel_model is not None:
            __deprecated__('"rel_model" has been deprecated in favor of '
                           '"model" for ForeignKeyField objects.')
            model = rel_model
        if to_field is not None:
            __deprecated__('"to_field" has been deprecated in favor of '
                           '"field" for ForeignKeyField objects.')
            field = to_field
        if related_name is not None:
            __deprecated__('"related_name" has been deprecated in favor of '
                           '"backref" for Field objects.')
            backref = related_name

        self.rel_model = model
        self.rel_field = field
        self.backref = backref
        self.on_delete = on_delete
        self.on_update = on_update
        self.deferred = _deferred
        self.object_id_name = object_id_name

    def __repr__(self):
        if hasattr(self, 'model') and getattr(self, 'name', None):
            return '<%s: "%s"."%s">' % (type(self).__name__,
                                        self.model._meta.name,
                                        self.name)
        return '<%s: (unbound)>' % type(self).__name__

    @property
    def field_type(self):
        if not isinstance(self.rel_field, AutoField):
            return self.rel_field.field_type
        return IntegerField.field_type

    def get_modifiers(self):
        if not isinstance(self.rel_field, AutoField):
            return self.rel_field.get_modifiers()
        return super(ForeignKeyField, self).get_modifiers()

    def coerce(self, value):
        return self.rel_field.coerce(value)

    def db_value(self, value):
        if isinstance(value, self.rel_model):
            value = value._get_pk_value()
        return self.rel_field.db_value(value)

    def python_value(self, value):
        if isinstance(value, self.rel_model):
            return value
        return self.rel_field.python_value(value)

    def expression(self):
        return self.column == self.rel_field.column

    def bind(self, model, name, set_attribute=True):
        if not self.column_name:
            self.column_name = name if name.endswith('_id') else name + '_id'
        if not self.object_id_name:
            self.object_id_name = self.column_name
            if self.object_id_name == name:
                self.object_id_name += '_id'
        elif self.object_id_name == name:
            raise ValueError('ForeignKeyField "%s"."%s" specifies an '
                             'object_id_name that conflicts with its field '
                             'name.' % (model._meta.name, name))
        if self.rel_model == 'self':
            self.rel_model = model
        if isinstance(self.rel_field, basestring):
            self.rel_field = getattr(self.rel_model, self.rel_field)
        elif self.rel_field is None:
            self.rel_field = self.rel_model._meta.primary_key

        if not self.backref:
            self.backref = '%s_set' % model._meta.name

        super(ForeignKeyField, self).bind(model, name, set_attribute)
        if set_attribute:
            setattr(model, self.object_id_name, ObjectIdAccessor(self))
            setattr(self.rel_model, self.backref, BackrefAccessor(self))

    def foreign_key_constraint(self):
        return NodeList((
            SQL('FOREIGN KEY'),
            EnclosedNodeList((self,)),
            SQL('REFERENCES'),
            self.rel_model,
            EnclosedNodeList((self.rel_field,))))

    def __getattr__(self, attr):
        if attr in self.rel_model._meta.fields:
            return self.rel_model._meta.fields[attr]
        raise AttributeError('%r has no attribute %s, nor is it a valid field '
                             'on %s.' % (self, attr, self.rel_model))


class DeferredForeignKey(Field):
    _unresolved = set()

    def __init__(self, rel_model_name, **kwargs):
        self.field_kwargs = kwargs
        self.rel_model_name = rel_model_name.lower()
        DeferredForeignKey._unresolved.add(self)
        super(DeferredForeignKey, self).__init__()

    __hash__ = object.__hash__

    def set_model(self, rel_model):
        field = ForeignKeyField(rel_model, _deferred=True, **self.field_kwargs)
        self.model._meta.add_field(self.name, field)

    @staticmethod
    def resolve(model_cls):
        unresolved = list(DeferredForeignKey._unresolved)
        for dr in unresolved:
            if dr.rel_model_name == model_cls.__name__.lower():
                dr.set_model(model_cls)
                DeferredForeignKey._unresolved.discard(dr)


class DeferredThroughModel(object):
    def set_field(self, model, field, name):
        self.model = model
        self.field = field
        self.name = name

    def set_model(self, through_model):
        self.field.through_model = through_model
        self.field.bind(self.model, self.name)


class MetaField(Field):
    column_name = default = model = name = None
    primary_key = False


class ManyToManyFieldAccessor(FieldAccessor):
    def __init__(self, model, field, name):
        super(ManyToManyFieldAccessor, self).__init__(model, field, name)
        self.model = field.model
        self.rel_model = field.rel_model
        self.through_model = field.get_through_model()
        self.src_fk = self.through_model._meta.model_refs[self.model][0]
        self.dest_fk = self.through_model._meta.model_refs[self.rel_model][0]

    def __get__(self, instance, instance_type=None):
        if instance is not None:
            return (ManyToManyQuery(instance, self, self.rel_model)
                    .join(self.through_model)
                    .join(self.model)
                    .where(self.src_fk == instance))
        return self.field

    def __set__(self, instance, value):
        query = self.__get__(instance)
        query.add(value, clear_existing=True)


class ManyToManyField(MetaField):
    accessor_class = ManyToManyFieldAccessor

    def __init__(self, rel_model, backref=None, through_model=None,
                 _is_backref=False):
        if through_model is not None and not (
                isinstance(through_model, DeferredThroughModel) or
                is_model(through_model)):
            raise TypeError('Unexpected value for through_model. Expected '
                            'Model or DeferredThroughModel.')
        self.rel_model = rel_model
        self.backref = backref
        self.through_model = through_model
        self._is_backref = _is_backref

    def _get_descriptor(self):
        return ManyToManyFieldAccessor(self)

    def bind(self, model, name, set_attribute=True):
        if isinstance(self.through_model, DeferredThroughModel):
            self.through_model.set_field(model, self, name)
            return

        super(ManyToManyField, self).bind(model, name, set_attribute)

        if not self._is_backref:
            many_to_many_field = ManyToManyField(
                self.model,
                through_model=self.through_model,
                _is_backref=True)
            backref = self.backref or model._meta.name + 's'
            many_to_many_field.bind(self.rel_model, backref)

    def get_models(self):
        return [model for _, model in sorted((
            (self._is_backref, self.model),
            (not self._is_backref, self.rel_model)))]

    def get_through_model(self):
        if not self.through_model:
            lhs, rhs = self.get_models()
            tables = [model._meta.table_name for model in (lhs, rhs)]

            class Meta:
                database = self.model._meta.database
                table_name = '%s_%s_through' % tuple(tables)
                indexes = (
                    ((lhs._meta.name, rhs._meta.name),
                     True),)

            attrs = {
                lhs._meta.name: ForeignKeyField(lhs),
                rhs._meta.name: ForeignKeyField(rhs)}
            attrs['Meta'] = Meta

            self.through_model = type(
                '%s%sThrough' % (lhs.__name__, rhs.__name__),
                (Model,),
                attrs)

        return self.through_model


class VirtualField(MetaField):
    field_class = None

    def __init__(self, field_class=None, *args, **kwargs):
        Field = field_class if field_class is not None else self.field_class
        self.field_instance = Field() if Field is not None else None
        super(VirtualField, self).__init__(*args, **kwargs)

    def db_value(self, value):
        if self.field_instance is not None:
            return self.field_instance.db_value(value)
        return value

    def python_value(self, value):
        if self.field_instance is not None:
            return self.field_instance.python_value(value)
        return value

    def bind(self, model, name, set_attribute=True):
        self.model = model
        self.column_name = self.name = name
        setattr(model, name, self.accessor_class(model, self, name))


class CompositeKey(MetaField):
    """A primary key composed of multiple columns."""
    sequence = None

    def __init__(self, *field_names):
        self.field_names = field_names

    def __get__(self, instance, instance_type=None):
        if instance is not None:
            return tuple([getattr(instance, field_name)
                          for field_name in self.field_names])
        return self

    def __set__(self, instance, value):
        if not isinstance(value, (list, tuple)):
            raise TypeError('A list or tuple must be used to set the value of '
                            'a composite primary key.')
        if len(value) != len(self.field_names):
            raise ValueError('The length of the value must equal the number '
                             'of columns of the composite primary key.')
        for idx, field_value in enumerate(value):
            setattr(instance, self.field_names[idx], field_value)

    def __eq__(self, other):
        expressions = [(self.model._meta.fields[field] == value)
                       for field, value in zip(self.field_names, other)]
        return reduce(operator.and_, expressions)

    def __ne__(self, other):
        return ~(self == other)

    def __hash__(self):
        return hash((self.model.__name__, self.field_names))

    def __sql__(self, ctx):
        return ctx.sql(CommaNodeList([self.model._meta.fields[field]
                                      for field in self.field_names]))

    def bind(self, model, name, set_attribute=True):
        self.model = model
        self.column_name = self.name = name
        setattr(model, self.name, self)


class _SortedFieldList(object):
    __slots__ = ('_keys', '_items')

    def __init__(self):
        self._keys = []
        self._items = []

    def __getitem__(self, i):
        return self._items[i]

    def __iter__(self):
        return iter(self._items)

    def __contains__(self, item):
        k = item._sort_key
        i = bisect_left(self._keys, k)
        j = bisect_right(self._keys, k)
        return item in self._items[i:j]

    def index(self, field):
        return self._keys.index(field._sort_key)

    def insert(self, item):
        k = item._sort_key
        i = bisect_left(self._keys, k)
        self._keys.insert(i, k)
        self._items.insert(i, item)

    def remove(self, item):
        idx = self.index(item)
        del self._items[idx]
        del self._keys[idx]


# MODELS


class SchemaManager(object):
    def __init__(self, model, database=None, **context_options):
        self.model = model
        self._database = database
        context_options.setdefault('scope', SCOPE_VALUES)
        self.context_options = context_options

    @property
    def database(self):
        return self._database or self.model._meta.database

    @database.setter
    def database(self, value):
        self._database = value

    def _create_context(self):
        return self.database.get_sql_context(**self.context_options)

    def _create_table(self, safe=True, **options):
        is_temp = options.pop('temporary', False)
        ctx = self._create_context()
        ctx.literal('CREATE TEMPORARY TABLE ' if is_temp else 'CREATE TABLE ')
        if safe:
            ctx.literal('IF NOT EXISTS ')
        ctx.sql(self.model).literal(' ')

        columns = []
        constraints = []
        meta = self.model._meta
        if meta.composite_key:
            pk_columns = [meta.fields[field_name].column
                          for field_name in meta.primary_key.field_names]
            constraints.append(NodeList((SQL('PRIMARY KEY'),
                                         EnclosedNodeList(pk_columns))))

        for field in meta.sorted_fields:
            columns.append(field.ddl(ctx))
            if isinstance(field, ForeignKeyField) and not field.deferred:
                constraints.append(field.foreign_key_constraint())

        if meta.constraints:
            constraints.extend(meta.constraints)

        constraints.extend(self._create_table_option_sql(options))
        ctx.sql(EnclosedNodeList(columns + constraints))

        if meta.without_rowid:
            ctx.literal(' WITHOUT ROWID')
        return ctx

    def _create_table_option_sql(self, options):
        accum = []
        options = merge_dict(self.model._meta.options or {}, options)
        if not options:
            return accum

        for key, value in sorted(options.items()):
            if not isinstance(value, Node):
                if is_model(value):
                    value = value._meta.table
                else:
                    value = SQL(value)
            accum.append(NodeList((SQL(key), value), glue='='))
        return accum

    def create_table(self, safe=True, **options):
        self.database.execute(self._create_table(safe=safe, **options))

    def _drop_table(self, safe=True, **options):
        is_temp = options.pop('temporary', False)
        ctx = self._create_context()
        return (ctx
                .literal('DROP TEMPORARY ' if is_temp else 'DROP ')
                .literal('TABLE IF EXISTS ' if safe else 'TABLE ')
                .sql(self.model))

    def drop_table(self, safe=True, **options):
        self.database.execute(self._drop_table(safe=safe), **options)

    def _create_indexes(self, safe=True):
        return [self._create_index(index, safe)
                for index in self.model._meta.fields_to_index()]

    def _create_index(self, index, safe=True):
        if isinstance(index, Index):
            if not self.database.safe_create_index:
                index = index.safe(False)
            elif index._safe != safe:
                index = index.safe(safe)
        return self._create_context().sql(index)

    def create_indexes(self, safe=True):
        for query in self._create_indexes(safe=safe):
            self.database.execute(query)

    def _drop_indexes(self, safe=True):
        return [self._drop_index(index, safe)
                for index in self.model._meta.fields_to_index()
                if isinstance(index, Index)]

    def _drop_index(self, index, safe):
        statement = 'DROP INDEX '
        if safe and self.database.safe_drop_index:
            statement += 'IF EXISTS '
        return (self
                ._create_context()
                .literal(statement)
                .sql(Entity(index._name)))

    def drop_indexes(self, safe=True):
        for query in self._drop_indexes(safe=safe):
            self.database.execute(query)

    def _check_sequences(self, field):
        if not field.sequence or not self.database.sequences:
            raise ValueError('Sequences are either not supported, or are not '
                             'defined for "%s".' % field.name)

    def _create_sequence(self, field):
        self._check_sequences(field)
        if not self.database.sequence_exists(field.sequence):
            return (self
                    ._create_context()
                    .literal('CREATE SEQUENCE ')
                    .sql(Entity(field.sequence)))

    def create_sequence(self, field):
        self.database.execute(self._create_sequence(field))

    def _drop_sequence(self, field):
        self._check_sequences(field)
        if self.database.sequence_exists(field.sequence):
            return (self
                    ._create_context()
                    .literal('DROP SEQUENCE ')
                    .sql(Entity(field.sequence)))

    def drop_sequence(self, field):
        self.database.execute(self._drop_sequence(field))

    def create_all(self, safe=True, **table_options):
        if self.database.sequences:
            for field in self.model._meta.sorted_fields:
                if field and field.sequence:
                    self.create_sequence(field)

        self.create_table(safe, **table_options)
        self.create_indexes(safe=safe)

    def drop_all(self, safe=True):
        self.drop_table(safe)


class Metadata(object):
    def __init__(self, model, database=None, table_name=None, indexes=None,
                 primary_key=None, constraints=None, schema=None,
                 only_save_dirty=False, table_alias=None, depends_on=None,
                 options=None, db_table=None, table_function=None,
                 without_rowid=False, **kwargs):
        if db_table is not None:
            __deprecated__('"db_table" has been deprecated in favor of '
                           '"table_name" for Models.')
            table_name = db_table
        self.model = model
        self.database = database

        self.fields = {}
        self.columns = {}
        self.combined = {}

        self._sorted_field_list = _SortedFieldList()
        self.sorted_fields = []
        self.sorted_field_names = []

        self.defaults = {}
        self._default_by_name = {}
        self._default_dict = {}
        self._default_callables = {}
        self._default_callable_list = []

        self.name = model.__name__.lower()
        self.table_function = table_function
        if not table_name:
            table_name = (self.table_function(model)
                          if self.table_function
                          else re.sub('[^\w]+', '_', self.name))
        self.table_name = table_name
        self._table = None

        self.indexes = list(indexes) if indexes else []
        self.constraints = constraints
        self.schema = schema
        self.primary_key = primary_key
        self.composite_key = self.auto_increment = None
        self.only_save_dirty = only_save_dirty
        self.table_alias = table_alias
        self.depends_on = depends_on
        self.without_rowid = without_rowid

        self.refs = {}
        self.backrefs = {}
        self.model_refs = defaultdict(list)
        self.model_backrefs = defaultdict(list)

        self.options = options or {}
        for key, value in kwargs.items():
            setattr(self, key, value)
        self._additional_keys = set(kwargs.keys())

    def model_graph(self, refs=True, backrefs=True, depth_first=True):
        if not refs and not backrefs:
            raise ValueError('One of `refs` or `backrefs` must be True.')

        accum = [(None, self.model, None)]
        seen = set()
        queue = deque((self,))
        method = queue.pop if depth_first else queue.popleft

        while queue:
            curr = method()
            if curr in seen: continue
            seen.add(curr)

            if refs:
                for fk, model in curr.refs.items():
                    accum.append((fk, model, False))
                    queue.append(model._meta)
            if backrefs:
                for fk, model in curr.backrefs.items():
                    accum.append((fk, model, True))
                    queue.append(model._meta)

        return accum

    def add_ref(self, field):
        rel = field.rel_model
        self.refs[field] = rel
        self.model_refs[rel].append(field)
        rel._meta.backrefs[field] = self.model
        rel._meta.model_backrefs[self.model].append(field)

    def remove_ref(self, field):
        rel = field.rel_model
        del self.refs[field]
        self.model_ref[rel].remove(field)
        del rel._meta.backrefs[field]
        rel._meta.model_backrefs[self.model].remove(field)

    @property
    def table(self):
        if self._table is None:
            self._table = Table(
                self.table_name,
                [field.column_name for field in self.sorted_fields],
                schema=self.schema,
                alias=self.table_alias,
                _model=self.model,
                _database=self.database)
        return self._table

    @table.setter
    def table(self):
        raise AttributeError('Cannot set the "table".')

    @table.deleter
    def table(self):
        self._table = None

    @property
    def entity(self):
        return Entity(self.table_name)

    def _update_sorted_fields(self):
        self.sorted_fields = list(self._sorted_field_list)
        self.sorted_field_names = [f.name for f in self.sorted_fields]

    def add_field(self, field_name, field, set_attribute=True):
        if field_name in self.fields:
            self.remove_field(field_name)

        if not isinstance(field, MetaField):
            del self.table
            field.bind(self.model, field_name, set_attribute)
            self.fields[field.name] = field
            self.columns[field.column_name] = field
            self.combined[field.name] = field
            self.combined[field.column_name] = field

            self._sorted_field_list.insert(field)
            self._update_sorted_fields()

            if field.default is not None:
                # This optimization helps speed up model instance construction.
                self.defaults[field] = field.default
                if callable(field.default):
                    self._default_callables[field] = field.default
                    self._default_callable_list.append((field.name,
                                                        field.default))
                else:
                    self._default_dict[field] = field.default
                    self._default_by_name[field.name] = field.default
        else:
            field.bind(self.model, field_name, set_attribute)

        if isinstance(field, ForeignKeyField):
            self.add_ref(field)

    def remove_field(self, field_name):
        if field_name not in self.fields:
            return

        del self.table
        original = self.fields.pop(field_name)
        del self.columns[original.column_name]
        del self.combined[field_name]
        try:
            del self.combined[original.column_name]
        except KeyError:
            pass
        self._sorted_field_list.remove(original)
        self._update_sorted_fields()

        if original.default is not None:
            del self.defaults[original]
            if self._default_callables.pop(original, None):
                for i, (name, _) in enumerate(self._default_callable_list):
                    if name == field_name:
                        self._default_callable_list.pop(i)
                        break
            else:
                self._default_dict.pop(original, None)
                self._default_by_name.pop(original.name, None)

        if isinstance(original, ForeignKeyField):
            self.remove_ref(original)

    def set_primary_key(self, name, field):
        self.composite_key = isinstance(field, CompositeKey)
        self.add_field(name, field)
        self.primary_key = field
        self.auto_increment = (
            field.auto_increment or
            bool(field.sequence))

    def get_primary_keys(self):
        if self.composite_key:
            return tuple([self.fields[field_name]
                          for field_name in self.primary_key.field_names])
        else:
            return (self.primary_key,)

    def get_default_dict(self):
        dd = self._default_by_name.copy()
        for field_name, default in self._default_callable_list:
            dd[field_name] = default()
        return dd

    def fields_to_index(self):
        indexes = []
        for f in self.sorted_fields:
            if f.primary_key:
                continue
            if f.index or f.unique or isinstance(f, ForeignKeyField):
                indexes.append(ModelIndex(self.model, (f,), unique=f.unique))

        for index_obj in self.indexes:
            if isinstance(index_obj, Node):
                indexes.append(index_obj)
            elif isinstance(index_obj, (list, tuple)):
                index_parts, unique = index_obj
                fields = []
                for part in index_parts:
                    if isinstance(part, basestring):
                        fields.append(self.combined[part])
                    elif isinstance(part, Node):
                        fields.append(part)
                    else:
                        raise ValueError('Expected either a field name or a '
                                         'subclass of Node. Got: %s' % part)
                indexes.append(ModelIndex(self.model, fields, unique=unique))

        return indexes

    def set_database(self, database):
        self.database = database
        self.model._schema._database = database


class SubclassAwareMetadata(Metadata):
    models = []

    def __init__(self, model, *args, **kwargs):
        super(SubclassAwareMetadata, self).__init__(model, *args, **kwargs)
        self.models.append(model)

    def map_models(self, fn):
        for model in self.models:
            fn(model)


class DoesNotExist(Exception): pass


class ModelBase(type):
    inheritable = set(['constraints', 'database', 'indexes', 'primary_key',
                       'options', 'schema', 'table_function'])

    def __new__(cls, name, bases, attrs):
        if name == MODEL_BASE or bases[0].__name__ == MODEL_BASE:
            return super(ModelBase, cls).__new__(cls, name, bases, attrs)

        meta_options = {}
        meta = attrs.pop('Meta', None)
        if meta:
            for k, v in meta.__dict__.items():
                if not k.startswith('_'):
                    meta_options[k] = v

        pk = getattr(meta, 'primary_key', None)
        pk_name = parent_pk = None

        # Inherit any field descriptors by deep copying the underlying field
        # into the attrs of the new model, additionally see if the bases define
        # inheritable model options and swipe them.
        for b in bases:
            if not hasattr(b, '_meta'):
                continue

            base_meta = b._meta
            if parent_pk is None:
                parent_pk = deepcopy(base_meta.primary_key)
            all_inheritable = cls.inheritable | base_meta._additional_keys
            for k in base_meta.__dict__:
                if k in all_inheritable and k not in meta_options:
                    meta_options[k] = base_meta.__dict__[k]

            for (k, v) in b.__dict__.items():
                if k in attrs: continue

                if isinstance(v, FieldAccessor) and not v.field.primary_key:
                    attrs[k] = deepcopy(v.field)

        sopts = meta_options.pop('schema_options', None) or {}
        Meta = meta_options.get('model_metadata_class', Metadata)
        Schema = meta_options.get('schema_manager_class', SchemaManager)

        # Construct the new class.
        cls = super(ModelBase, cls).__new__(cls, name, bases, attrs)
        cls.__data__ = cls.__rel__ = None

        cls._meta = Meta(cls, **meta_options)
        cls._schema = Schema(cls, **sopts)

        fields = []
        for key, value in cls.__dict__.items():
            if isinstance(value, Field):
                if value.primary_key and pk:
                    raise ValueError('over-determined primary key %s.' % name)
                elif value.primary_key:
                    pk, pk_name = value, key
                else:
                    fields.append((key, value))

        if pk is None:
            if parent_pk is not False:
                pk, pk_name = ((parent_pk, parent_pk.name)
                               if parent_pk is not None else
                               (AutoField(), 'id'))
            else:
                pk = False
        elif isinstance(pk, CompositeKey):
            pk_name = '__composite_key__'
            cls._meta.composite_key = True

        if pk is not False:
            cls._meta.set_primary_key(pk_name, pk)

        for name, field in fields:
            cls._meta.add_field(name, field)

        # Create a repr and error class before finalizing.
        if hasattr(cls, '__unicode__'):
            setattr(cls, '__repr__', lambda self: '<%s: %r>' % (
                cls.__name__, self.__unicode__()))

        exc_name = '%sDoesNotExist' % cls.__name__
        exc_attrs = {'__module__': cls.__module__}
        exception_class = type(exc_name, (DoesNotExist,), exc_attrs)
        cls.DoesNotExist = exception_class

        # Call validation hook, allowing additional model validation.
        cls.validate_model()
        DeferredForeignKey.resolve(cls)
        return cls

    def __iter__(self):
        return iter(self.select())

    def __getitem__(self, key):
        return self.get_by_id(key)

    def __setitem__(self, key, value):
        self.set_by_id(key, value)

    def __delitem__(self, key):
        self.delete_by_id(key)

    def __contains__(self, key):
        try:
            self.get_by_id(key)
        except self.DoesNotExist:
            return False
        else:
            return True


class _BoundModelsContext(_callable_context_manager):
    def __init__(self, models, database, bind_refs, bind_backrefs):
        self.models = models
        self.database = database
        self.bind_refs = bind_refs
        self.bind_backrefs = bind_backrefs

    def __enter__(self):
        self._orig_database = []
        for model in self.models:
            self._orig_database.append(model._meta.database)
            model.bind(self.database, self.bind_refs, self.bind_backrefs)
        return self.models

    def __exit__(self, exc_type, exc_val, exc_tb):
        for model, db in zip(self.models, self._orig_database):
            model.bind(db, self.bind_refs, self.bind_backrefs)


class Model(with_metaclass(ModelBase, Node)):
    def __init__(self, *args, **kwargs):
        if kwargs.pop('__no_default__', None):
            self.__data__ = {}
        else:
            self.__data__ = self._meta.get_default_dict()
        self._dirty = set(self.__data__)
        self.__rel__ = {}

        for k in kwargs:
            setattr(self, k, kwargs[k])

    @classmethod
    def validate_model(cls):
        pass

    @classmethod
    def alias(cls, alias=None):
        return ModelAlias(cls, alias)

    @classmethod
    def select(cls, *fields):
        is_default = not fields
        if not fields:
            fields = cls._meta.sorted_fields
        return ModelSelect(cls, fields, is_default=is_default)

    @classmethod
    def _normalize_data(cls, data, kwargs):
        normalized = {}
        if data:
            if not isinstance(data, dict):
                if kwargs:
                    raise ValueError('Data cannot be mixed with keyword '
                                     'arguments: %s' % data)
                return data
            for key in data:
                try:
                    field = (key if isinstance(key, Field)
                             else cls._meta.combined[key])
                except KeyError:
                    raise ValueError('Unrecognized field name: "%s" in %s.' %
                                     (key, data))
                normalized[field] = data[key]
        if kwargs:
            for key in kwargs:
                try:
                    normalized[cls._meta.combined[key]] = kwargs[key]
                except KeyError:
                    normalized[getattr(cls, key)] = kwargs[key]
        return normalized

    @classmethod
    def update(cls, __data=None, **update):
        return ModelUpdate(cls, cls._normalize_data(__data, update))

    @classmethod
    def insert(cls, __data=None, **insert):
        return ModelInsert(cls, cls._normalize_data(__data, insert))

    @classmethod
    def insert_many(cls, rows, fields=None):
        return ModelInsert(cls, insert=rows, columns=fields)

    @classmethod
    def insert_from(cls, query, fields):
        columns = [getattr(cls, field) if isinstance(field, basestring)
                   else field for field in fields]
        return ModelInsert(cls, insert=query, columns=columns)

    @classmethod
    def replace(cls, __data=None, **insert):
        return cls.insert(__data, **insert).on_conflict('REPLACE')

    @classmethod
    def replace_many(cls, rows, fields=None):
        return (cls
                .insert_many(rows=rows, fields=fields)
                .on_conflict('REPLACE'))

    @classmethod
    def raw(cls, sql, *params):
        return ModelRaw(cls, sql, params)

    @classmethod
    def delete(cls):
        return ModelDelete(cls)

    @classmethod
    def create(cls, **query):
        inst = cls(**query)
        inst.save(force_insert=True)
        return inst

    @classmethod
    def noop(cls):
        return NoopModelSelect(cls, ())

    @classmethod
    def get(cls, *query, **filters):
        sq = cls.select()
        if query:
            sq = sq.where(*query)
        if filters:
            sq = sq.filter(**filters)
        return sq.get()

    @classmethod
    def get_or_none(cls, *query, **filters):
        try:
            return cls.get(*query, **filters)
        except DoesNotExist:
            pass

    @classmethod
    def get_by_id(cls, pk):
        return cls.get(cls._meta.primary_key == pk)

    @classmethod
    def set_by_id(cls, key, value):
        return cls.update(value).where(cls._meta.primary_key == key).execute()

    @classmethod
    def delete_by_id(cls, pk):
        return cls.delete().where(cls._meta.primary_key == pk).execute()

    @classmethod
    def get_or_create(cls, **kwargs):
        defaults = kwargs.pop('defaults', {})
        query = cls.select()
        for field, value in kwargs.items():
            query = query.where(getattr(cls, field) == value)

        try:
            return query.get(), False
        except cls.DoesNotExist:
            try:
                if defaults:
                    kwargs.update(defaults)
                with cls._meta.database.atomic():
                    return cls.create(**kwargs), True
            except IntegrityError as exc:
                try:
                    return query.get(), False
                except cls.DoesNotExist:
                    raise exc

    @classmethod
    def filter(cls, *dq_nodes, **filters):
        return cls.select().filter(*dq_nodes, **filters)

    def get_id(self):
        return getattr(self, self._meta.primary_key.name)

    _pk = property(get_id)

    @_pk.setter
    def _pk(self, value):
        setattr(self, self._meta.primary_key.name, value)

    def _pk_expr(self):
        return self._meta.primary_key == self._pk

    def _prune_fields(self, field_dict, only):
        new_data = {}
        for field in only:
            if isinstance(field, basestring):
                field = self._meta.combined[field]
            if field.name in field_dict:
                new_data[field.name] = field_dict[field.name]
        return new_data

    def _populate_unsaved_relations(self, field_dict):
        for foreign_key in self._meta.refs:
            conditions = (
                foreign_key in self._dirty and
                foreign_key in field_dict and
                field_dict[foreign_key] is None and
                self.__rel__.get(foreign_key) is not None)
            if conditions:
                setattr(self, foreign_key, getattr(self, foreign_key))
                field_dict[foreign_key] = self._data[foreign_key]

    def save(self, force_insert=False, only=None):
        field_dict = self.__data__.copy()
        if self._meta.primary_key is not False:
            pk_field = self._meta.primary_key
            pk_value = self._pk
        else:
            pk_field = pk_value = None
        if only:
            field_dict = self._prune_fields(field_dict, only)
        elif self._meta.only_save_dirty and not force_insert:
            field_dict = self._prune_fields(field_dict, self.dirty_fields)
            if not field_dict:
                self._dirty.clear()
                return False

        self._populate_unsaved_relations(field_dict)
        if pk_value is not None and not force_insert:
            if self._meta.composite_key:
                for pk_part_name in pk_field.field_names:
                    field_dict.pop(pk_part_name, None)
            else:
                field_dict.pop(pk_field.name, None)
            rows = self.update(**field_dict).where(self._pk_expr()).execute()
        elif pk_field is None or not self._meta.auto_increment:
            self.insert(**field_dict).execute()
            rows = 1
        else:
            pk_from_cursor = self.insert(**field_dict).execute()
            if pk_from_cursor is not None:
                pk_value = pk_from_cursor
            self._pk = pk_value
            rows = 1
        self._dirty.clear()
        return rows

    def is_dirty(self):
        return bool(self._dirty)

    @property
    def dirty_fields(self):
        return [f for f in self._meta.sorted_fields if f.name in self._dirty]

    def dependencies(self, search_nullable=False):
        model_class = type(self)
        query = self.select(self._meta.primary_key).where(self._pk_expr())
        stack = [(type(self), query)]
        seen = set()

        while stack:
            klass, query = stack.pop()
            if klass in seen:
                continue
            seen.add(klass)
            for fk, rel_model in klass._meta.backrefs.items():
                if rel_model is model_class:
                    node = (fk == self.__data__[fk.rel_field.name])
                else:
                    node = fk << query
                subquery = (rel_model.select(rel_model._meta.primary_key)
                            .where(node))
                if not fk.null or search_nullable:
                    stack.append((rel_model, subquery))
                yield (node, fk)

    def delete_instance(self, recursive=False, delete_nullable=False):
        if recursive:
            dependencies = self.dependencies(delete_nullable)
            for query, fk in reversed(list(dependencies)):
                model = fk.model
                if fk.null and not delete_nullable:
                    model.update(**{fk.name: None}).where(query).execute()
                else:
                    model.delete().where(query).execute()
        return self.delete().where(self._pk_expr()).execute()

    def __hash__(self):
        return hash((self.__class__, self._pk))

    def __eq__(self, other):
        return (
            other.__class__ == self.__class__ and
            self._pk is not None and
            other._pk == self._pk)

    def __ne__(self, other):
        return not self == other

    def __sql__(self, ctx):
        return ctx.sql(getattr(self, self._meta.primary_key.name))

    @classmethod
    def bind(cls, database, bind_refs=True, bind_backrefs=True):
        is_different = cls._meta.database is not database
        cls._meta.database = database
        if bind_refs or bind_backrefs:
            G = cls._meta.model_graph(refs=bind_refs, backrefs=bind_backrefs)
            for _, model, is_backref in G:
                model._meta.database = database
        return is_different

    @classmethod
    def bind_ctx(cls, database, bind_refs=True, bind_backrefs=True):
        return _BoundModelsContext((cls,), database, bind_refs, bind_backrefs)

    @classmethod
    def table_exists(cls):
        return cls._meta.database.table_exists(cls._meta.table)

    @classmethod
    def create_table(cls, safe=True, **options):
        if 'fail_silently' in options:
            __deprecated__('"fail_silently" has been deprecated in favor of '
                           '"safe" for the create_table() method.')
            safe = options.pop('fail_silently')

        if safe and not cls._meta.database.safe_create_index \
           and cls.table_exists():
            return
        cls._schema.create_all(safe, **options)

    @classmethod
    def drop_table(cls, safe=True):
        if safe and not cls._meta.database.safe_drop_index \
           and not cls.table_exists():
            return
        cls._schema.drop_all(safe)

    @classmethod
    def add_index(cls, *fields, **kwargs):
        if len(fields) == 1 and isinstance(fields[0], SQL):
            cls._meta.indexes.append(fields[0])
        else:
            cls._meta.indexes.append(ModelIndex(cls, fields, **kwargs))


class ModelAlias(Node):
    def __init__(self, model, alias=None):
        self.__dict__['model'] = model
        self.__dict__['alias'] = alias

    def __getattr__(self, attr):
        model_attr = getattr(self.model, attr)
        if isinstance(model_attr, Field):
            self.__dict__[attr] = FieldAlias.create(self, model_attr)
            return self.__dict__[attr]
        return model_attr

    def __setattr__(self, attr, value):
        raise AttributeError('Cannot set attributes on model aliases.')

    def get_field_aliases(self):
        return [getattr(self, n) for n in self.model._meta.sorted_field_names]

    def select(self, *selection):
        if not selection:
            selection = self.get_field_aliases()
        return ModelSelect(self, selection)

    def __call__(self, **kwargs):
        return self.model(**kwargs)

    def __sql__(self, ctx):
        if ctx.scope == SCOPE_VALUES:
            # Return the quoted table name.
            return ctx.sql(self.model)

        if self.alias:
            ctx.alias_manager[self] = self.alias

        if ctx.scope == SCOPE_SOURCE:
            # Define the table and its alias.
            return (ctx
                    .sql(Entity(self.model._meta.table_name))
                    .literal(' AS ')
                    .sql(Entity(ctx.alias_manager[self])))
        else:
            # Refer to the table using the alias.
            return ctx.sql(Entity(ctx.alias_manager[self]))


class FieldAlias(Field):
    def __init__(self, source, field):
        self.source = source
        self.model = source.model
        self.field = field

    @classmethod
    def create(cls, source, field):
        class _FieldAlias(cls, type(field)):
            pass
        return _FieldAlias(source, field)

    def clone(self):
        return FieldAlias(self.source, self.field)

    def coerce(self, value): return self.field.coerce(value)
    def python_value(self, value): return self.field.python_value(value)
    def db_value(self, value): return self.field.db_value(value)
    def __getattr__(self, attr):
        return self.source if attr == 'model' else getattr(self.field, attr)

    def __sql__(self, ctx):
        return ctx.sql(Column(self.source, self.field.column_name))


def sort_models(models):
    models = set(models)
    seen = set()
    ordering = []
    def dfs(model):
        if model in models and model not in seen:
            seen.add(model)
            for rel_model in model._meta.refs.values():
                dfs(rel_model)
            if model._meta.depends_on:
                for dependency in model._meta.depends_on:
                    dfs(dependency)
            ordering.append(model)

    names = lambda m: (m._meta.name, m._meta.table_name)
    for m in sorted(models, key=names):
        dfs(m)
    return ordering


class _ModelQueryHelper(object):
    default_row_type = ROW.MODEL

    def __init__(self, *args, **kwargs):
        super(_ModelQueryHelper, self).__init__(*args, **kwargs)
        if not self._database:
            self._database = self.model._meta.database

    def _get_cursor_wrapper(self, cursor):
        row_type = self._row_type or self.default_row_type
        if row_type == ROW.MODEL:
            return self._get_model_cursor_wrapper(cursor)
        elif row_type == ROW.DICT:
            return ModelDictCursorWrapper(cursor, self.model, self._returning)
        elif row_type == ROW.TUPLE:
            return ModelTupleCursorWrapper(cursor, self.model, self._returning)
        elif row_type == ROW.NAMED_TUPLE:
            return ModelNamedTupleCursorWrapper(cursor, self.model,
                                                self._returning)
        elif row_type == ROW.CONSTRUCTOR:
            return ModelObjectCursorWrapper(cursor, self.model,
                                            self._returning, self._constructor)
        else:
            raise ValueError('Unrecognized row type: "%s".' % row_type)

    def _get_model_cursor_wrapper(self, cursor):
        return ModelObjectCursorWrapper(cursor, self.model, [], self.model)


class ModelRaw(_ModelQueryHelper, RawQuery):
    def __init__(self, model, sql, params, **kwargs):
        self.model = model
        super(ModelRaw, self).__init__(sql=sql, params=params, **kwargs)

    def get(self):
        try:
            return self.execute()[0]
        except IndexError:
            sql, params = self.sql()
            raise self.model.DoesNotExist('%s instance matching query does '
                                          'not exist:\nSQL: %s\nParams: %s' %
                                          (self.model, sql, params))


class BaseModelSelect(_ModelQueryHelper):
    def __add__(self, rhs):
        return ModelCompoundSelectQuery(self.model, self, 'UNION ALL', rhs)

    def __or__(self, rhs):
        return ModelCompoundSelectQuery(self.model, self, 'UNION', rhs)

    def __and__(self, rhs):
        return ModelCompoundSelectQuery(self.model, self, 'INTERSECT', rhs)

    def __sub__(self, rhs):
        return ModelCompoundSelectQuery(self.model, self, 'EXCEPT', rhs)

    def __iter__(self):
        if not self._cursor_wrapper:
            self.execute()
        return iter(self._cursor_wrapper)

    def prefetch(self, *subqueries):
        return prefetch(self, *subqueries)

    def get(self):
        clone = self.paginate(1, 1)
        clone._cursor_wrapper = None
        try:
            return clone.execute()[0]
        except IndexError:
            sql, params = clone.sql()
            raise self.model.DoesNotExist('%s instance matching query does '
                                          'not exist:\nSQL: %s\nParams: %s' %
                                          (clone.model, sql, params))

    @Node.copy
    def group_by(self, *columns):
        grouping = []
        for column in columns:
            if is_model(column):
                grouping.extend(column._meta.sorted_fields)
            else:
                grouping.append(column)
        self._group_by = grouping


class ModelCompoundSelectQuery(BaseModelSelect, CompoundSelectQuery):
    def __init__(self, model, *args, **kwargs):
        self.model = model
        super(ModelCompoundSelectQuery, self).__init__(*args, **kwargs)


class ModelSelect(BaseModelSelect, Select):
    def __init__(self, model, fields_or_models, is_default=False):
        self.model = self._join_ctx = model
        self._joins = {}
        self._is_default = is_default
        fields = []
        for fm in fields_or_models:
            if is_model(fm):
                fields.extend(fm._meta.sorted_fields)
            elif isinstance(fm, ModelAlias):
                fields.extend(fm.get_field_aliases())
            elif isinstance(fm, Table) and fm._columns:
                fields.extend([getattr(fm, col) for col in fm._columns])
            else:
                fields.append(fm)
        super(ModelSelect, self).__init__([model], fields)

    def switch(self, ctx=None):
        self._join_ctx = ctx
        return self

    def objects(self, constructor=None):
        self._row_type = ROW.CONSTRUCTOR
        self._constructor = self.model if constructor is None else constructor
        return self

    def _get_model(self, src):
        if is_model(src):
            return src, True
        elif isinstance(src, Table) and src._model:
            return src._model, False
        elif isinstance(src, ModelAlias):
            return src.model, False
        return None, False

    def _normalize_join(self, src, dest, on, attr):
        # Allow "on" expression to have an alias that determines the
        # destination attribute for the joined data.
        on_alias = isinstance(on, Alias)
        if on_alias:
            attr = on._alias
            on = on.alias()

        src_model, src_is_model = self._get_model(src)
        dest_model, dest_is_model = self._get_model(dest)
        if src_model and dest_model:
            self._join_ctx = dest
            constructor = dest_model

            if not (src_is_model and dest_is_model) and isinstance(on, Column):
                if on.source is src:
                    to_field = src_model._meta.columns[on.name]
                elif on.source is dest:
                    to_field = dest_model._meta.columns[on.name]
                else:
                    raise AttributeError('"on" clause Column %s does not '
                                         'belong to %s or %s.' %
                                         (on, src_model, dest_model))
                on = None
            elif isinstance(on, Field):
                to_field = on
                on = None
            else:
                to_field = None

            fk_field, is_backref = self._generate_on_clause(
                src_model, dest_model, to_field, on)

            if on is None:
                src_attr = 'name' if src_is_model else 'column_name'
                dest_attr = 'name' if dest_is_model else 'column_name'
                if is_backref:
                    lhs = getattr(dest, getattr(fk_field, dest_attr))
                    rhs = getattr(src, getattr(fk_field.rel_field, src_attr))
                else:
                    lhs = getattr(src, getattr(fk_field, src_attr))
                    rhs = getattr(dest, getattr(fk_field.rel_field, dest_attr))
                on = (lhs == rhs)

            if not attr:
                if fk_field is not None and not is_backref:
                    attr = fk_field.name
                else:
                    attr = dest_model._meta.name

        elif isinstance(dest, Source):
            constructor = dict
            attr = attr or dest._alias
            if not attr and isinstance(dest, Table):
                attr = attr or dest.__name__

        return (on, attr, constructor)

    @Node.copy
    def join(self, dest, join_type='INNER', on=None, src=None, attr=None):
        src = self._join_ctx if src is None else src

        on, attr, constructor = self._normalize_join(src, dest, on, attr)
        if attr:
            self._joins.setdefault(src, [])
            self._joins[src].append((dest, attr, constructor))

        return super(ModelSelect, self).join(dest, join_type, on)

    @Node.copy
    def join_from(self, src, dest, join_type='INNER', on=None, attr=None):
        return self.join(dest, join_type, on, src, attr)

    def _generate_on_clause(self, src, dest, to_field=None, on=None):
        meta = src._meta
        backref = fk_fields = False
        if dest in meta.model_refs:
            fk_fields = meta.model_refs[dest]
        elif dest in meta.model_backrefs:
            fk_fields = meta.model_backrefs[dest]
            backref = True

        if not fk_fields:
            if on is not None:
                return None, False
            raise ValueError('Unable to find foreign key between %s and %s. '
                             'Please specify an explicit join condition.' %
                             (src, dest))
        if to_field is not None:
            target = (to_field.field if isinstance(to_field, FieldAlias)
                      else to_field)
            fk_fields = [f for f in fk_fields if (
                         (f is target) or
                         (backref and f.rel_field is to_field))]

        if len(fk_fields) > 1:
            if on is None:
                raise ValueError('More than one foreign key between %s and %s.'
                                 ' Please specify which you are joining on.' %
                                 (src, dest))
            return None, False
        else:
            fk_field = fk_fields[0]

        return fk_field, backref

    def _get_model_cursor_wrapper(self, cursor):
        if len(self._from_list) == 1 and not self._joins:
            return ModelObjectCursorWrapper(cursor, self.model,
                                            self._returning, self.model)
        return ModelCursorWrapper(cursor, self.model, self._returning,
                                  self._from_list, self._joins)

    def ensure_join(self, lm, rm, on=None, **join_kwargs):
        join_ctx = self._join_ctx
        for dest, attr, constructor in self._joins.get(lm, []):
            if dest == rm:
                return self
        return self.switch(lm).join(rm, on=on, **join_kwargs).switch(join_ctx)

    def convert_dict_to_node(self, qdict):
        accum = []
        joins = []
        fks = (ForeignKeyField, BackrefAccessor)
        for key, value in sorted(qdict.items()):
            curr = self.model
            if '__' in key and key.rsplit('__', 1)[1] in DJANGO_MAP:
                key, op = key.rsplit('__', 1)
                op = DJANGO_MAP[op]
            elif value is None:
                op = OP.IS
            else:
                op = OP.EQ

            if '__' not in key:
                # Handle simplest case. This avoids joining over-eagerly when a
                # direct FK lookup is all that is required.
                model_attr = getattr(curr, key)
            else:
                for piece in key.split('__'):
                    for dest, attr, _ in self._joins.get(curr, ()):
                        if attr == piece or (isinstance(dest, ModelAlias) and
                                             dest.alias == piece):
                            curr = dest
                            break
                    else:
                        model_attr = getattr(curr, piece)
                        if value is not None and isinstance(model_attr, fks):
                            curr = model_attr.rel_model
                            joins.append(model_attr)
            accum.append(Expression(model_attr, op, value))
        return accum, joins

    def filter(self, *args, **kwargs):
        # normalize args and kwargs into a new expression
        dq_node = ColumnBase()
        if args:
            dq_node &= reduce(operator.and_, [a.clone() for a in args])
        if kwargs:
            dq_node &= DQ(**kwargs)

        # dq_node should now be an Expression, lhs = Node(), rhs = ...
        q = deque([dq_node])
        dq_joins = set()
        while q:
            curr = q.popleft()
            if not isinstance(curr, Expression):
                continue
            for side, piece in (('lhs', curr.lhs), ('rhs', curr.rhs)):
                if isinstance(piece, DQ):
                    query, joins = self.convert_dict_to_node(piece.query)
                    dq_joins.update(joins)
                    expression = reduce(operator.and_, query)
                    # Apply values from the DQ object.
                    if piece._negated:
                        expression = Negated(expression)
                    #expression._alias = piece._alias
                    setattr(curr, side, expression)
                else:
                    q.append(piece)

        dq_node = dq_node.rhs

        query = self.clone()
        for field in dq_joins:
            if isinstance(field, ForeignKeyField):
                lm, rm = field.model, field.rel_model
                field_obj = field
            elif isinstance(field, ReverseRelationDescriptor):
                lm, rm = field.field.rel_model, field.rel_model
                field_obj = field.field
            query = query.ensure_join(lm, rm, field_obj)
        return query.where(dq_node)

    def __sql_selection__(self, ctx, is_subquery=False):
        if self._is_default and is_subquery and len(self._returning) > 1 and \
           self.model._meta.primary_key is not False:
            return ctx.sql(self.model._meta.primary_key)

        return ctx.sql(CommaNodeList(self._returning))


class NoopModelSelect(ModelSelect):
    def __sql__(self, ctx):
        return self.model._meta.database.get_noop_select(ctx)

    def _get_cursor_wrapper(self, cursor):
        return CursorWrapper(cursor)


class _ModelWriteQueryHelper(_ModelQueryHelper):
    def __init__(self, model, *args, **kwargs):
        self.model = model
        super(_ModelWriteQueryHelper, self).__init__(model, *args, **kwargs)

    def _set_table_alias(self, ctx):
        table = self.model._meta.table
        ctx.alias_manager[table] = table.__name__


class ModelUpdate(_ModelWriteQueryHelper, Update):
    pass


class ModelInsert(_ModelWriteQueryHelper, Insert):
    def __init__(self, *args, **kwargs):
        super(ModelInsert, self).__init__(*args, **kwargs)
        if self._returning is None and self.model._meta.database is not None:
            if self.model._meta.database.returning_clause:
                self._returning = self.model._meta.get_primary_keys()
                self._row_type = ROW.TUPLE


class ModelDelete(_ModelWriteQueryHelper, Delete):
    pass


class ManyToManyQuery(ModelSelect):
    def __init__(self, instance, accessor, rel, *args, **kwargs):
        self._instance = instance
        self._accessor = accessor
        super(ManyToManyQuery, self).__init__(rel, (rel,), *args, **kwargs)

    def _id_list(self, model_or_id_list):
        if isinstance(model_or_id_list[0], Model):
            return [obj._pk for obj in model_or_id_list]
        return model_or_id_list

    def add(self, value, clear_existing=False):
        if clear_existing:
            self.clear()

        accessor = self._accessor
        if isinstance(value, SelectQuery):
            query = value.columns(
                SQL(str(self._instance._pk)),
                accessor.rel_model._meta.primary_key)
            accessor.through_model.insert_from(
                fields=[accessor.src_fk, accessor.dest_fk],
                query=query).execute()
        else:
            value = ensure_tuple(value)
            if not value:
                return
            inserts = [{
                accessor.src_fk.name: self._instance._pk,
                accessor.dest_fk.name: rel_id}
                for rel_id in self._id_list(value)]
            accessor.through_model.insert_many(inserts).execute()

    def remove(self, value):
        if isinstance(value, SelectQuery):
            subquery = value.columns(value.model._meta.primary_key)
            return (self._accessor.through_model
                    .delete()
                    .where(
                        (self._accessor.dest_fk << subquery) &
                        (self._accessor.src_fk == self._instance._pk))
                    .execute())
        else:
            value = ensure_tuple(value)
            if not value:
                return
            return (self._accessor.through_model
                    .delete()
                    .where(
                        (self._accessor.dest_fk << self._id_list(value)) &
                        (self._accessor.src_fk == self._instance._pk))
                    .execute())

    def clear(self):
        return (self._accessor.through_model
                .delete()
                .where(self._accessor.src_fk == self._instance)
                .execute())


class BaseModelCursorWrapper(DictCursorWrapper):
    def __init__(self, cursor, model, columns):
        super(BaseModelCursorWrapper, self).__init__(cursor)
        self.model = model
        self.select = columns or []

    def _initialize_columns(self):
        combined = self.model._meta.combined
        table = self.model._meta.table
        description = self.cursor.description

        self.ncols = len(self.cursor.description)
        self.columns = []
        self.converters = converters = [None] * self.ncols
        self.fields = fields = [None] * self.ncols

        for idx, description_item in enumerate(description):
            column = description_item[0]
            dot_index = column.find('.')
            if dot_index != -1:
                column = column[dot_index + 1:]

            self.columns.append(column)
            try:
                node = self.select[idx]
            except IndexError:
                continue
            else:
                node = node.unwrap()

            # Heuristics used to attempt to get the field associated with a
            # given SELECT column, so that we can accurately convert the value
            # returned by the database-cursor into a Python object.
            if isinstance(node, Field):
                converters[idx] = node.python_value
                fields[idx] = node
            elif column in combined:
                if not (isinstance(node, Function) and not node._coerce):
                    # Unlikely, but if a function was aliased to a column,
                    # don't use that column's converter if coerce is False.
                    converters[idx] = combined[column].python_value
                if isinstance(node, Column) and node.source == table:
                    fields[idx] = combined[column]
            elif (isinstance(node, Function) and node.arguments and
                  node._coerce):
                # Try to special-case functions calling fields.
                first = node.arguments[0]
                if isinstance(first, Node):
                    first = first.unwrap()

                if isinstance(first, Field):
                    converters[idx] = first.python_value
                elif isinstance(first, Entity):
                    path = first.path[-1]
                    field = combined.get(path)
                    if field is not None:
                        converters[idx] = field.python_value

    initialize = _initialize_columns

    def process_row(self, row):
        raise NotImplementedError


class ModelDictCursorWrapper(BaseModelCursorWrapper):
    def process_row(self, row):
        result = {}
        columns, converters = self.columns, self.converters

        for i in range(self.ncols):
            if converters[i] is not None:
                result[columns[i]] = converters[i](row[i])
            else:
                result[columns[i]] = row[i]

        return result


class ModelTupleCursorWrapper(ModelDictCursorWrapper):
    constructor = tuple

    def process_row(self, row):
        columns, converters = self.columns, self.converters
        return self.constructor([
            (converters[i](row[i]) if converters[i] is not None else row[i])
            for i in range(self.ncols)])


class ModelNamedTupleCursorWrapper(ModelTupleCursorWrapper):
    def initialize(self):
        self._initialize_columns()
        self.tuple_class = namedtuple('Row', self.columns)
        self.constructor = lambda row: self.tuple_class(*row)


class ModelObjectCursorWrapper(ModelDictCursorWrapper):
    def __init__(self, cursor, model, select, constructor):
        self.constructor = constructor
        self.is_model = is_model(constructor)
        super(ModelObjectCursorWrapper, self).__init__(cursor, model, select)

    def process_row(self, row):
        data = super(ModelObjectCursorWrapper, self).process_row(row)
        if self.is_model:
            # Clear out any dirty fields before returning to the user.
            obj = self.constructor(__no_default__=1, **data)
            obj._dirty.clear()
            return obj
        else:
            return self.constructor(**data)


class ModelCursorWrapper(BaseModelCursorWrapper):
    def __init__(self, cursor, model, select, from_list, joins):
        super(ModelCursorWrapper, self).__init__(cursor, model, select)
        self.from_list = from_list
        self.joins = joins

    def initialize(self):
        self._initialize_columns()
        select, columns = self.select, self.columns

        self.key_to_constructor = {self.model: self.model}
        self.src_to_dest = []
        accum = deque(self.from_list)
        while accum:
            curr = accum.popleft()
            if isinstance(curr, Join):
                accum.append(curr.lhs)
                accum.append(curr.rhs)
                continue

            if curr not in self.joins:
                continue

            for key, attr, constructor in self.joins[curr]:
                if key not in self.key_to_constructor:
                    self.key_to_constructor[key] = constructor
                    self.src_to_dest.append((curr, attr, key,
                                             constructor is dict))
                    accum.append(key)

        self.column_keys = []
        for idx, node in enumerate(select):
            key = self.model
            field = self.fields[idx]
            if field is not None:
                if isinstance(field, FieldAlias):
                    key = field.source
                else:
                    key = field.model
            else:
                if isinstance(node, Node):
                    node = node.unwrap()
                if isinstance(node, Column):
                    key = node.source

            self.column_keys.append(key)

    def process_row(self, row):
        objects = {}
        object_list = []
        for key, constructor in self.key_to_constructor.items():
            objects[key] = constructor(__no_default__=True)
            object_list.append(objects[key])

        set_keys = set()
        for idx, key in enumerate(self.column_keys):
            instance = objects[key]
            column = self.columns[idx]
            value = row[idx]
            if value is not None:
                set_keys.add(key)
            if self.converters[idx]:
                value = self.converters[idx](value)

            if isinstance(instance, dict):
                instance[column] = value
            else:
                setattr(instance, column, value)

        # Need to do some analysis on the joins before this.
        for (src, attr, dest, is_dict) in self.src_to_dest:
            instance = objects[src]
            try:
                joined_instance = objects[dest]
            except KeyError:
                continue

            # If no fields were set on the destination instance then do not
            # assign an "empty" instance.
            if instance is None or dest is None or (dest not in set_keys):
                continue

            if is_dict:
                instance[attr] = joined_instance
            else:
                setattr(instance, attr, joined_instance)

        # When instantiating models from a cursor, we clear the dirty fields.
        for instance in object_list:
            instance._dirty.clear()

        return objects[self.model]


class PrefetchQuery(namedtuple('_PrefetchQuery', (
    'query', 'fields', 'is_backref', 'rel_models', 'field_to_name', 'model'))):
    def __new__(cls, query, fields=None, is_backref=None, rel_models=None,
                field_to_name=None, model=None):
        if fields:
            if is_backref:
                rel_models = [field.model for field in fields]
                foreign_key_attrs = [field.rel_field.name for field in fields]
            else:
                rel_models = [field.rel_model for field in fields]
                foreign_key_attrs = [field.name for field in fields]
            field_to_name = list(zip(fields, foreign_key_attrs))
        model = query.model
        return super(PrefetchQuery, cls).__new__(
            cls, query, fields, is_backref, rel_models, field_to_name, model)

    def populate_instance(self, instance, id_map):
        if self.is_backref:
            for field in self.fields:
                identifier = instance.__data__[field.name]
                key = (field, identifier)
                if key in id_map:
                    setattr(instance, field.name, id_map[key])
        else:
            for field, attname in self.field_to_name:
                identifier = instance.__data__[field.rel_field.name]
                key = (field, identifier)
                rel_instances = id_map.get(key, [])
                for inst in rel_instances:
                    setattr(inst, attname, instance)
                setattr(instance, field.backref, rel_instances)

    def store_instance(self, instance, id_map):
        for field, attname in self.field_to_name:
            identity = field.rel_field.python_value(instance.__data__[attname])
            key = (field, identity)
            if self.is_backref:
                id_map[key] = instance
            else:
                id_map.setdefault(key, [])
                id_map[key].append(instance)


def prefetch_add_subquery(sq, subqueries):
    fixed_queries = [PrefetchQuery(sq)]
    for i, subquery in enumerate(subqueries):
        if isinstance(subquery, tuple):
            subquery, target_model = subquery
        else:
            target_model = None
        if not isinstance(subquery, Query) and is_model(subquery) or \
           isinstance(subquery, ModelAlias):
            subquery = subquery.select()
        subquery_model = subquery.model
        fks = backrefs = None
        for j in reversed(range(i + 1)):
            fixed = fixed_queries[j]
            last_query = fixed.query
            last_model = fixed.model
            rels = subquery_model._meta.model_refs.get(last_model, [])
            if rels:
                fks = [getattr(subquery_model, fk.name) for fk in rels]
                pks = [getattr(last_model, fk.rel_field.name) for fk in rels]
            else:
                backrefs = last_model._meta.model_refs.get(subquery_model, [])
            if (fks or backrefs) and ((target_model is last_model) or
                                      (target_model is None)):
                break

        if not fks and not backrefs:
            tgt_err = ' using %s' % target_model if target_model else ''
            raise AttributeError('Error: unable to find foreign key for '
                                 'query: %s%s' % (subquery, tgt_err))

        if fks:
            expr = reduce(operator.or_, [
                (fk << last_query.select(pk))
                for (fk, pk) in zip(fks, pks)])
            subquery = subquery.where(expr)
            fixed_queries.append(PrefetchQuery(subquery, fks, False))
        elif backrefs:
            expr = reduce(operator.or_, [
                (backref.rel_field << last_query.select(backref))
                for backref in backrefs])
            subquery = subquery.where(expr)
            fixed_queries.append(PrefetchQuery(subquery, backrefs, True))

    return fixed_queries


def prefetch(sq, *subqueries):
    if not subqueries:
        return sq

    fixed_queries = prefetch_add_subquery(sq, subqueries)
    deps = {}
    rel_map = {}
    for pq in reversed(fixed_queries):
        query_model = pq.model
        if pq.fields:
            for rel_model in pq.rel_models:
                rel_map.setdefault(rel_model, [])
                rel_map[rel_model].append(pq)

        deps[query_model] = {}
        id_map = deps[query_model]
        has_relations = bool(rel_map.get(query_model))

        for instance in pq.query:
            if pq.fields:
                pq.store_instance(instance, id_map)
            if has_relations:
                for rel in rel_map[query_model]:
                    rel.populate_instance(instance, deps[rel.model])

    return pq.query
