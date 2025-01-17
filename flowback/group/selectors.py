# TODO groups, groupusers, groupinvites, groupuserinvites,
#  groupdefaultpermission, grouppermissions, grouptags, groupuserdelegates
import django_filters
from typing import Union
from django.db.models import Q, Exists, OuterRef, Count
from django.forms import model_to_dict

from flowback.common.services import get_object
from flowback.kanban.selectors import kanban_entry_list
from flowback.user.models import User
from flowback.group.models import Group, GroupUser, GroupUserInvite, GroupPermissions, GroupTags, GroupUserDelegator, \
    GroupUserDelegatePool
from flowback.schedule.selectors import schedule_event_list
from rest_framework.exceptions import ValidationError


def group_default_permissions(*, group: Union[Group, int]):
    if isinstance(group, int):
        group = get_object(Group, id=group)

    if group.default_permission:
        return model_to_dict(group.default_permission)

    fields = GroupPermissions._meta.get_fields()
    fields = [field for field in fields if not field.auto_created
              and field.name not in ['created_at', 'updated_at', 'role_name', 'author']]

    defaults = dict()
    for field in fields:
        defaults[field.name] = field.default

    return defaults


def group_user_permissions(*,
                           user: Union[User, int] = None,
                           group: Union[Group, int] = None,
                           group_user: [GroupUser, int] = None,
                           permissions: list[str] = None,
                           raise_exception: bool = True) -> Union[GroupUser, bool]:

    if type(user) == int:
        user = get_object(User, id=user)

    if type(group) == int:
        group = get_object(Group, id=group)

    permissions = permissions or []

    if user and group:
        group_user = get_object(GroupUser, 'User is not in group', group=group, user=user)

    elif group_user:
        if type(group_user) == int:
            group_user = get_object(GroupUser, id=group_user)

    else:
        raise Exception('group_user_permissions is missing appropiate parameters')

    perobj = GroupPermissions()
    user_permissions = model_to_dict(group_user.permission) if group_user.permission else group_default_permissions(group=group_user.group)

    # Check if admin permission is present
    if 'admin' in permissions:
        if group_user.is_admin or group_user.group.created_by == group_user.user or group_user.user.is_superuser:
            return group_user

    # Check if creator permission is present
    if 'creator' in permissions:
        if group_user.group.created_by == group_user.user or group_user.user.is_superuser:
            return group_user

    validated_permissions = any([user_permissions.get(key, False) for key in permissions]) or not permissions
    if not validated_permissions:
        if raise_exception:
            raise ValidationError(f'Permission denied, requires one of following permissions: {", ".join(permissions)})')
        else:
            return False

    return group_user


class BaseGroupFilter(django_filters.FilterSet):
    joined = django_filters.BooleanFilter(lookup_expr='exact')

    class Meta:
        model = Group
        fields = dict(id=['exact'],
                      name=['exact', 'icontains'],
                      direct_join=['exact'])


class BaseGroupUserFilter(django_filters.FilterSet):
    username__icontains = django_filters.CharFilter(field_name='user__username', lookup_expr='icontains')
    delegate = django_filters.BooleanFilter(field_name='delegate')

    class Meta:
        model = GroupUser
        fields = dict(id=['exact'],
                      user_id=['exact'],
                      is_admin=['exact'],
                      permission=['in'])


class BaseGroupUserInviteFilter(django_filters.FilterSet):
    username__icontains = django_filters.CharFilter(field_name='user__username', lookup_expr='icontains')

    class Meta:
        model = GroupUser
        fields = ['user', 'group']


class BaseGroupPermissionsFilter(django_filters.FilterSet):
    class Meta:
        model = GroupPermissions
        fields = dict(id=['exact'], role_name=['exact', 'icontains'])


class BaseGroupTagsFilter(django_filters.FilterSet):
    class Meta:
        model = GroupTags
        fields = dict(id=['exact'],
                      tag_name=['exact', 'icontains'],
                      active=['exact'])


class BaseGroupUserDelegatePoolFilter(django_filters.FilterSet):
    id = django_filters.NumberFilter()
    delegate_id = django_filters.NumberFilter(field_name='groupuserdelegate__id')
    group_user_id = django_filters.NumberFilter(field_name='groupuserdelegate__group_user_id')


class BaseGroupUserDelegateFilter(django_filters.FilterSet):
    delegate_id = django_filters.NumberFilter()
    delegate_user_id = django_filters.NumberFilter(field_name='delegate__user_id')
    delegate_name__icontains = django_filters.CharFilter(field_name='delegate__user__username__icontains')
    tag_id = django_filters.NumberFilter(field_name='tags__tag_id')
    tag_name = django_filters.CharFilter(field_name='tags__tag_name')
    tag_name__icontains = django_filters.CharFilter(field_name='tags__tag_name', lookup_expr='icontains')

    class Meta:
        model = GroupUserDelegator
        fields = ['delegate_id']


def _group_get_visible_for(user: User):
    query = Q(public=True) | Q(Q(public=False) & Q(groupuser__user__in=[user]))
    return Group.objects.filter(query)


def group_list(*, fetched_by: User, filters=None):
    filters = filters or {}
    joined_groups = Group.objects.filter(id=OuterRef('pk'), groupuser__user__in=[fetched_by])
    qs = _group_get_visible_for(user=fetched_by).annotate(joined=Exists(joined_groups),
                                                          member_count=Count('groupuser')).order_by('created_at').all()
    qs = BaseGroupFilter(filters, qs).qs
    return qs


def group_kanban_entry_list(*, fetched_by: User, group_id: int, filters=None):
    group_user = group_user_permissions(group=group_id, user=fetched_by)
    return kanban_entry_list(kanban_id=group_user.group.kanban.id, filters=filters, subscriptions=False)


def group_detail(*, fetched_by: User, group_id: int):
    group_user = group_user_permissions(group=group_id, user=fetched_by)
    return Group.objects.annotate(member_count=Count('groupuser')).get(id=group_user.group.id)


def group_schedule_event_list(*, fetched_by: User, group_id: int, filters=None):
    filters = filters or {}
    group_user = group_user_permissions(group=group_id, user=fetched_by)
    return schedule_event_list(schedule_id=group_user.group.schedule.id, filters=filters)


def group_user_list(*, group: int, fetched_by: User, filters=None):
    group_user_permissions(group=group, user=fetched_by)
    filters = filters or {}
    is_delegate = GroupUser.objects.filter(group_id=group, groupuserdelegate__group_user=OuterRef('pk'),
                                           groupuserdelegate__group=OuterRef('group'))
    qs = GroupUser.objects.filter(group_id=group).annotate(delegate=Exists(is_delegate)).all()
    return BaseGroupUserFilter(filters, qs).qs


def group_user_delegate_pool_list(*, group: int, fetched_by: User, filters=None):
    group_user_permissions(group=group, user=fetched_by)
    filters = filters or {}
    qs = GroupUserDelegatePool.objects.filter(group=group).all()
    return BaseGroupUserDelegatePoolFilter(filters, qs).qs


def group_user_invite_list(*, group: int, fetched_by: User, filters=None):
    if group:
        group_user_permissions(group=group, user=fetched_by, permissions=['invite_user', 'admin'])
        qs = GroupUserInvite.objects.filter(group_id=group).all()

    else:
        qs = GroupUserInvite.objects.filter(user=fetched_by).all()

    filters = filters or {}
    return BaseGroupUserFilter(filters, qs).qs


def group_permissions_list(*, group: int, fetched_by: User, filters=None):
    group_user_permissions(group=group, user=fetched_by)
    filters = filters or {}
    qs = GroupPermissions.objects.filter(author_id=group).all()
    return BaseGroupPermissionsFilter(filters, qs).qs


def group_tags_list(*, group: int, fetched_by: User, filters=None):
    filters = filters or {}
    group_user_permissions(group=group, user=fetched_by)
    query = Q(group_id=group, active=True)
    if group_user_permissions(group=group, user=fetched_by, permissions=['admin'], raise_exception=False):
        query = Q(group_id=group)

    qs = GroupTags.objects.filter(query).all()
    return BaseGroupTagsFilter(filters, qs).qs


def group_user_delegate_list(*, group: int, fetched_by: User, filters=None):
    filters = filters or {}
    fetched_by = group_user_permissions(group=group, user=fetched_by)
    query = Q(group_id=group, delegator_id=fetched_by)

    qs = GroupUserDelegator.objects.filter(query).all()
    return BaseGroupUserDelegateFilter(filters, qs).qs
