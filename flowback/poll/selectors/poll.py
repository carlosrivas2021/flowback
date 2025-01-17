from typing import Union

import django_filters
from django.db.models import Q, Exists, OuterRef, Count
from django.utils import timezone

from flowback.comment.models import Comment
from flowback.group.models import Group
from flowback.poll.models import Poll
from flowback.user.models import User
from flowback.group.selectors import group_user_permissions


class BasePollFilter(django_filters.FilterSet):
    order_by = django_filters.OrderingFilter(fields=(('start_date', 'start_date_asc'),
                                                     ('-start_date', 'start_date_desc')))
    start_date = django_filters.DateFromToRangeFilter()
    end_date = django_filters.DateFromToRangeFilter()
    tag_name = django_filters.CharFilter(lookup_expr=['exact', 'icontains'], field_name='tag__name')

    class Meta:
        model = Poll
        fields = dict(id=['exact'],
                      created_by=['exact'],
                      title=['exact', 'icontains'],
                      poll_type=['exact'],
                      public=['exact'],
                      tag=['exact'],
                      finished=['exact'],
                      pinned=['exact'])


# TODO order_by(pinned, param)
def poll_list(*, fetched_by: User, group_id: Union[int, None], filters=None):
    filters = filters or {}

    if group_id:
        group_user_permissions(group=group_id, user=fetched_by)
        qs = Poll.objects.filter(created_by__group_id=group_id)\
            .annotate(total_comments=Count('comment_section__comment', filters=dict(active=True))).all()

    else:
        joined_groups = Group.objects.filter(id=OuterRef('created_by__group_id'), groupuser__user__in=[fetched_by])
        qs = Poll.objects.filter(
            (Q(created_by__group__groupuser__user__in=[fetched_by]) |
             Q(public=True) & ~Q(created_by__group__groupuser__user__in=[fetched_by])
             ) & Q(start_date__lte=timezone.now())
        ).annotate(group_joined=Exists(joined_groups), total_comments=Count('comment_section__comment',
                                                                            filters=dict(active=True))).all()

    return BasePollFilter(filters, qs).qs
