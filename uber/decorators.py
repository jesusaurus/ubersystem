from uber.common import *

def check_if_can_reg(func):
    @wraps(func)
    def with_check(*args,**kwargs):
        if not DEV_BOX:
            if state.BADGES_SOLD >= MAX_BADGE_SALES:
                return render('static_views/prereg_soldout.html')
            elif state.BEFORE_PREREG_OPEN:
                return render('static_views/prereg_not_yet_open.html')
            elif state.AFTER_PREREG_TAKEDOWN:
                return render('static_views/prereg_closed.html')
        return func(*args,**kwargs)
    return with_check


def get_innermost(func):
    return get_innermost(func.__wrapped__) if hasattr(func, '__wrapped__') else func


def site_mappable(func):
    func.site_mappable = True
    return func


def suffix_property(func):
    func._is_suffix_property = True
    return func

def _suffix_property_check(inst, name):
    if not name.startswith('_'):
        suffix = '_' + name.rsplit('_', 1)[-1]
        prop_func = getattr(inst, suffix, None)
        if getattr(prop_func, '_is_suffix_property', False):
            field_name = name[:-len(suffix)]
            field_val = getattr(inst, field_name)
            return prop_func(field_name, field_val)

suffix_property.check = _suffix_property_check


def csrf_protected(func):
    @wraps(func)
    def protected(*args, csrf_token, **kwargs):
        check_csrf(csrf_token)
        return func(*args, **kwargs)
    return protected


def ajax(func):
    '''decorator for Ajax POST requests which require a CSRF token and return JSON'''
    @wraps(func)
    def returns_json(*args, **kwargs):
        cherrypy.response.headers['Content-Type'] = 'application/json'
        assert cherrypy.request.method == 'POST', 'POST required, got {}'.format(cherrypy.request.method)
        check_csrf(kwargs.pop('csrf_token', None))
        return json.dumps(func(*args, **kwargs), cls=serializer).encode('utf-8')
    return returns_json


def ajax_gettable(func):
    '''
    Decorator for page handlers which return JSON.  Unlike the above @ajax decorator,
    this allows either GET or POST and does not check for a CSRF token, so this can
    be used for pages which supply data to external APIs as well as pages used for
    periodically polling the server for new data by our own Javascript code.
    '''
    @wraps(func)
    def returns_json(*args, **kwargs):
        cherrypy.response.headers['Content-Type'] = 'application/json'
        return json.dumps(func(*args, **kwargs), cls=serializer).encode('utf-8')
    return returns_json


def csv_file(func):
    @wraps(func)
    def csvout(self, session):
        cherrypy.response.headers['Content-Type'] = 'application/csv'
        cherrypy.response.headers['Content-Disposition'] = 'attachment; filename=' + func.__name__ + '.csv'
        writer = StringIO()
        func(self, csv.writer(writer), session)
        return writer.getvalue().encode('utf-8')
    return csvout


def check_shutdown(func):
    @wraps(func)
    def with_check(self, *args, **kwargs):
        if UBER_SHUT_DOWN:
            raise HTTPRedirect('index?message={}', 'The page you requested is only available pre-event.')
        else:
            return func(self, *args, **kwargs)
    return with_check


def credit_card(func):
    @wraps(func)
    def charge(self, session, payment_id, stripeToken, stripeEmail='ignored', **ignored):
        if ignored:
            log.error('received unexpected stripe parameters: {}', ignored)
        try:
            return func(self, session=session, payment_id=payment_id, stripeToken=stripeToken)
        except HTTPRedirect:
            raise
        except:
            send_email(ADMIN_EMAIL, [ADMIN_EMAIL, 'dom@magfest.org'], 'MAGFest Stripe error',
                       'Got an error while calling charge(self, payment_id={!r}, stripeToken={!r}, ignored={}):\n{}'
                       .format(payment_id, stripeToken, ignored, traceback.format_exc()))
            return traceback.format_exc()
    return charge


def sessionized(func):
    @wraps(func)
    def with_session(*args, **kwargs):
        innermost = get_innermost(func)
        if 'session' not in inspect.getfullargspec(innermost).args:
            return func(*args, **kwargs)
        else:
            with Session() as session:
                try:
                    retval = func(*args, session=session, **kwargs)
                    session.expunge_all()
                    return retval
                except HTTPRedirect:
                    session.commit()
                    raise
    return with_session


def renderable_data(data=None):
    data = data or {}
    data['PAGE'] = cherrypy.request.path_info.split('/')[-1]
    data.update({m.__name__: m for m in Session.all_models()})
    data.update({k: v for k,v in config.__dict__.items() if re.match('^[_A-Z0-9]*$', k)})
    data.update({k: getattr(state, k) for k in dir(state) if re.match('^[_A-Z0-9]*$', k)})
    for date in DATES:
        before, after = 'BEFORE_' + date, 'AFTER_' + date
        data.update({
            before: getattr(state, before),
            after:  getattr(state, after)
        })
    try:
        data['CSRF_TOKEN'] = cherrypy.session['csrf_token']
    except:
        pass

    access = AdminAccount.access_set()
    for acctype in ['ACCOUNTS','PEOPLE','STUFF','MONEY','CHALLENGES','CHECKINS']:
        data['HAS_' + acctype + '_ACCESS'] = getattr(config, acctype) in access

    return data

# render using the first template that actually exists in template_name_list
def render(template_name_list, data=None):
    data = renderable_data(data)
    template = loader.select_template(listify(template_name_list))
    rendered = template.render(Context(data))
    rendered = screw_you_nick(rendered, template)  # lolz.
    return rendered.encode('utf-8')


# this is a Magfest inside joke.
# Nick gets mad when people call Magfest a 'convention'. He always says 'It's not a convention, it's a festival'
# So........ if Nick is logged in.... let's annoy him a bit :)
def screw_you_nick(rendered, template):
    if not AT_THE_CON and AdminAccount.is_nick() and 'emails' not in template and 'history' not in template and 'form' not in rendered:
        return rendered.replace('festival', 'convention').replace('Fest', 'Con') # lolz.
    else:
        return rendered


def _get_module_name(class_or_func):
    return class_or_func.__module__.split('.')[-1]

def _get_template_filename(func):
    return os.path.join(_get_module_name(func), func.__name__ + '.html')

def renderable(func):
    @wraps(func)
    def with_rendering(*args, **kwargs):
        result = func(*args, **kwargs)
        if isinstance(result, dict):
            return render(_get_template_filename(func), result)
        else:
            return result
    return with_rendering

def unrestricted(func):
    func.restricted = False
    return func

def restricted(func):
    @wraps(func)
    def with_restrictions(*args, **kwargs):
        if func.restricted:
            if func.restricted == (SIGNUPS,):
                if not cherrypy.session.get('staffer_id'):
                    raise HTTPRedirect('../signups/login?message=You+are+not+logged+in')

            elif cherrypy.session.get('account_id') is None:
                raise HTTPRedirect('../accounts/login?message=You+are+not+logged+in')

            else:
                if not set(func.restricted).intersection(AdminAccount.access_set()):
                    if len(func.restricted) == 1:
                        return 'You need {} access for this page'.format(dict(ACCESS_OPTS)[func.restricted[0]])
                    else:
                        return ('You need at least one of the following access levels to view this page: '
                            + ', '.join(dict(ACCESS_OPTS)[r] for r in func.restricted))

        return func(*args, **kwargs)
    return with_restrictions

class all_renderable:
    def __init__(self, *needs_access):
        self.needs_access = needs_access

    def __call__(self, klass):
        for name,func in klass.__dict__.items():
            if hasattr(func, '__call__'):
                func.restricted = getattr(func, 'restricted', self.needs_access)
                new_func = sessionized(restricted(renderable(func)))
                new_func.exposed = True
                setattr(klass, name, new_func)
        return klass


register = template.Library()
def tag(klass):
    @register.tag(klass.__name__)
    def tagged(parser, token):
        return klass(*token.split_contents()[1:])
    return klass
