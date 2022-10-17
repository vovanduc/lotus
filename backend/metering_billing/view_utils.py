from decimal import Decimal

from dateutil import parser
from metering_billing.billable_metrics import METRIC_HANDLER_MAP
from metering_billing.models import Subscription
from metering_billing.payment_providers import PAYMENT_PROVIDER_MAP
from metering_billing.utils import (
    METRIC_TYPES,
    REVENUE_CALC_GRANULARITY,
    SUB_STATUS_TYPES,
    convert_to_decimal,
    periods_bwn_twodates,
)


def get_metric_usage(
    metric,
    query_start_date,
    query_end_date,
    granularity,
    customer=None,
    billable_only=False,
):
    handler = METRIC_HANDLER_MAP[metric.metric_type](metric)

    usage = handler.get_usage(
        granularity=granularity,
        start_date=query_start_date,
        end_date=query_end_date,
        customer=customer,
        billable_only=billable_only,
    )

    return usage


def calculate_sub_pc_usage_revenue(
    plan_component,
    billable_metric,
    customer,
    plan_start_date,
    plan_end_date,
    revenue_granularity=REVENUE_CALC_GRANULARITY.TOTAL,
):
    assert isinstance(
        revenue_granularity, REVENUE_CALC_GRANULARITY
    ), "revenue_granularity must be part of REVENUE_CALC_GRANULARITY enum"
    if type(plan_start_date) == str:
        plan_start_date = parser.parse(plan_start_date).date()
    if type(plan_end_date) == str:
        plan_end_date = parser.parse(plan_end_date).date()

    usage = get_metric_usage(
        billable_metric,
        plan_start_date,
        plan_end_date,
        revenue_granularity,
        customer=customer,
        billable_only=True,
    )

    usage = usage.get(customer.name, {})

    period_revenue_dict = {
        period: {}
        for period in periods_bwn_twodates(
            revenue_granularity, plan_start_date, plan_end_date
        )
    }
    free_units_usage_left = plan_component.free_metric_units
    remainder_billable_units = 0
    for period in period_revenue_dict:
        period_usage = usage.get(period, 0)
        qty = convert_to_decimal(period_usage)
        period_revenue_dict[period] = {"usage_qty": qty, "revenue": 0}
        if (
            plan_component.cost_per_batch == 0
            or plan_component.cost_per_batch is None
            or plan_component.metric_units_per_batch == 0
            or plan_component.metric_units_per_batch is None
        ):
            continue
        else:
            billable_units = max(
                qty - free_units_usage_left + remainder_billable_units, 0
            )
            billable_batches = billable_units // plan_component.metric_units_per_batch
            remainder_billable_units = (
                billable_units
                - billable_batches * plan_component.metric_units_per_batch
            )
            free_units_usage_left = max(0, free_units_usage_left - qty)
            if billable_metric.metric_type == METRIC_TYPES.STATEFUL:
                usage_revenue = (
                    billable_batches
                    * plan_component.cost_per_batch
                    / len(period_revenue_dict)
                )
            else:
                usage_revenue = billable_batches * plan_component.cost_per_batch
            period_revenue_dict[period]["revenue"] = convert_to_decimal(usage_revenue)
            if billable_metric.metric_type == METRIC_TYPES.STATEFUL:
                free_units_usage_left = plan_component.free_metric_units
                remainder_billable_units = 0
    return period_revenue_dict


# AGGREGATE USAGE + REVENUE METHODS
def get_subscription_usage_and_revenue(subscription):
    sub_dict = {}
    sub_dict["components"] = []
    # set up the billing plan for this subscription
    plan = subscription.billing_plan
    # set up other details of the subscription
    plan_start_date = subscription.start_date
    plan_end_date = subscription.end_date
    # extract other objects that we need when calculating usage
    customer = subscription.customer
    plan_components_qs = plan.components.all()
    # For each component of the plan, calculate usage/revenue
    for plan_component in plan_components_qs:
        plan_component_summary = calculate_sub_pc_usage_revenue(
            plan_component,
            plan_component.billable_metric,
            customer,
            plan_start_date,
            plan_end_date,
        )
        sub_dict["components"].append((plan_component.pk, plan_component_summary))
    sub_dict["usage_revenue_due"] = Decimal(0)
    for component_pk, component_dict in sub_dict["components"]:
        for date, date_dict in component_dict.items():
            sub_dict["usage_revenue_due"] += date_dict["revenue"]
    sub_dict["flat_revenue_due"] = subscription.billing_plan.flat_rate.amount
    sub_dict["total_revenue_due"] = (
        sub_dict["flat_revenue_due"] + sub_dict["usage_revenue_due"]
    )
    return sub_dict


def get_customer_usage_and_revenue(customer):
    customer_subscriptions = (
        Subscription.objects.filter(
            customer=customer,
            status=SUB_STATUS_TYPES.ACTIVE,
            organization=customer.organization,
        )
        .select_related("customer")
        .prefetch_related("billing_plan__components")
        .prefetch_related("billing_plan__components__billable_metric")
        .select_related("billing_plan")
    )
    subscription_usages = {"subscriptions": []}
    for subscription in customer_subscriptions:
        sub_dict = get_subscription_usage_and_revenue(subscription)
        del sub_dict["components"]
        sub_dict["billing_plan_name"] = subscription.billing_plan.name
        subscription_usages["subscriptions"].append(sub_dict)

    return subscription_usages


def sync_payment_provider_customers(organization):
    """
    For every payment provider an organization has, sync all customers
    """
    ret = []
    for pp_name, connector in PAYMENT_PROVIDER_MAP.items():
        if connector.organization_connected(organization):
            connector.import_customers(organization)
            ret.append(pp_name)
    return ret