# -*- coding: utf-8 -*-
###########################################################################
#    Module Writen to OpenERP, Open Source Management Solution
#
#    Copyright (c) 2012 Vauxoo - http://www.vauxoo.com
#    All Rights Reserved.
#    info@vauxoo.com
############################################################################
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from openerp.tools.translate import _
from openerp.osv import osv
from openerp.tools import float_compare
import openerp
import time


class account_bank_statement_line(osv.osv):

    _inherit = 'account.bank.statement.line'

    def _get_exchange_lines(self, cr, uid, parent, move_line_counterpart, move_line_payment_tax, amount_residual, company_currency, current_currency, context=None):
        '''
        Prepare the two lines in company currency due to currency rate difference.

        :param line: browse record of the voucher.line for which we want to create currency rate difference accounting
            entries
        :param move_id: Account move wher the move lines will be.
        :param amount_residual: Amount to be posted.
        :param company_currency: id of currency of the company to which the voucher belong
        :param current_currency: id of currency of the voucher
        :return: the account move line and its counterpart to create, depicted as mapping between fieldname and value
        :rtype: tuple of dict
        '''
        if amount_residual > 0:
            account_id = parent.company_id.expense_currency_exchange_account_id # modelo parent
            if not account_id:
                model, action_id = self.pool['ir.model.data'].get_object_reference(cr, uid, 'account', 'action_account_form')
                msg = _("You should configure the 'Loss Exchange Rate Account' to manage automatically the booking of accounting entries related to differences between exchange rates.")
                raise openerp.exceptions.RedirectWarning(msg, action_id, _('Go to the configuration panel'))
        else:
            account_id = parent.company_id.income_currency_exchange_account_id # modelo parent
            if not account_id:
                model, action_id = self.pool['ir.model.data'].get_object_reference(cr, uid, 'account', 'action_account_form')
                msg = _("You should configure the 'Gain Exchange Rate Account' to manage automatically the booking of accounting entries related to differences between exchange rates.")
                raise openerp.exceptions.RedirectWarning(msg, action_id, _('Go to the configuration panel'))
        # Even if the amount_currency is never filled, we need to pass the foreign currency because otherwise
        # the receivable/payable account may have a secondary currency, which render this field mandatory
        if move_line_counterpart.account_id.currency_id:
            account_currency_id = move_line_counterpart.account_id.currency_id.id
        else:
            account_currency_id = company_currency <> current_currency and current_currency or False
        move_line = {
            'journal_id': move_line_payment_tax.journal_id.id,
            'period_id': move_line_payment_tax.period_id.id,
            'name': _('change')+': '+(move_line_counterpart.ref or '/'),
            'account_id': move_line_counterpart.account_id.id, # move_line
            'move_id': move_line_payment_tax.move_id.id,
            'partner_id': move_line_payment_tax.partner_id.id, # modelo parent
            'currency_id': account_currency_id,
            'amount_currency': 0.0,
            'quantity': 1,
            'credit': amount_residual > 0 and amount_residual or 0.0,
            'debit': amount_residual < 0 and -amount_residual or 0.0,
            'date': parent.date, # modelo parent
        }
        move_line_counterpart = {
            'journal_id': move_line_payment_tax.journal_id.id,
            'period_id': move_line_payment_tax.period_id.id,
            'name': _('change')+': '+(move_line_counterpart.ref or '/'),
            'account_id': account_id.id,
            'move_id': move_line_payment_tax.move_id.id,
            'amount_currency': 0.0,
            'partner_id': move_line_payment_tax.partner_id.id,
            'currency_id': account_currency_id,
            'quantity': 1,
            'debit': amount_residual > 0 and amount_residual or 0.0,
            'credit': amount_residual < 0 and -amount_residual or 0.0,
            'date': parent.date,
        }
        return (move_line, move_line_counterpart)

    # pylint: disable=W0622
    def process_reconciliation(self, cr, uid, id, mv_line_dicts, context=None):

        if context is None:
            context = {}

        move_line_obj = self.pool.get('account.move.line')
        move_obj = self.pool.get('account.move')
        voucher_obj = self.pool.get('account.voucher')

        move_line_ids = []
        move_reconcile_id = []

        st_line = self.browse(cr, uid, id, context=context)
        company_currency = st_line.journal_id.company_id.currency_id.id
        statement_currency = st_line.journal_id.currency.id or company_currency

        context['date'] = st_line.date

        vals_move = {
            'date': time.strftime('%Y-%m-%d'),
            'period_id': st_line.statement_id.period_id.id,
            'journal_id': st_line.statement_id.journal_id.id,
            }
        move_id_old = move_obj.create(cr, uid, vals_move, context)

        self._get_factor_type(cr, uid, st_line.amount, False, context=context)
        type = 'sale'
        if st_line.amount < 0:
            type = 'payment'

        for move_line_dict in mv_line_dicts:
            move_amount_counterpart = self._get_move_line_counterpart(
                cr, uid, [move_line_dict], company_currency,
                statement_currency, context=context)
            move_line_tax_dict = self._get_move_line_tax(
                cr, uid, [move_line_dict], context=context)

            factor = voucher_obj.get_percent_pay_vs_invoice(
                cr, uid, move_amount_counterpart[1],
                move_amount_counterpart[0], context=context)

            for move_line_tax in move_line_tax_dict:
                move_line_rec = []
                line_tax_id = move_line_tax.get('tax_id')
                # Cuando el impuesto (@tax_id) tiene @amount = 0 es un impuesto
                # de compra 0% o EXENTO y necesitamos enviar el monto base
                amount_base_secondary =\
                    line_tax_id.amount and\
                    move_amount_counterpart[1] / (1+line_tax_id.amount) or\
                    move_line_tax.get('amount_base_secondary')
                account_tax_voucher =\
                    move_line_tax.get('account_tax_voucher')
                account_tax_collected =\
                    move_line_tax.get('account_tax_collected')
                amount_total_tax = move_line_tax.get('amount', 0)

                lines_tax = voucher_obj._preparate_move_line_tax(
                    cr, uid,
                    account_tax_voucher,  # cuenta del impuesto(account.tax)
                    account_tax_collected,  # cuenta del impuesto para notas de credito/debito(account.tax)
                    move_id_old, type,
                    st_line.partner_id.id,
                    st_line.statement_id.period_id.id,
                    st_line.statement_id.journal_id.id,
                    st_line.date, company_currency,
                    amount_total_tax * factor,  # Monto del impuesto por el factor(cuanto le corresponde)(aml)
                    amount_total_tax * factor,  # Monto del impuesto por el factor(cuanto le corresponde)(aml)
                    statement_currency, False,
                    move_line_tax.get('tax_id'),  # Impuesto
                    move_line_tax.get('tax_analytic_id'),  # Cuenta analitica del impuesto(aml)
                    amount_base_secondary,  # Monto base(aml)
                    factor, context=context)
                for move_line_dict_tax in lines_tax:
                    move_tax = move_line_obj.create(
                        cr, uid, move_line_dict_tax, context=context
                        )
                    move_line_ids.append(move_tax)
                    move_line_rec.append(move_tax)

                move_rec_exch = self._get_exchange_reconcile(
                    cr, uid, move_line_tax, move_line_rec,
                    move_amount_counterpart[0], move_amount_counterpart[2],
                    st_line.statement_id, company_currency,
                    statement_currency, context=context)

                move_line_ids.extend(move_rec_exch[0])
                move_reconcile_id.append(move_rec_exch[1])

        res = super(account_bank_statement_line, self).process_reconciliation(
            cr, uid, id, mv_line_dicts, context=context)

        move_line_obj.write(cr, uid, move_line_ids,
                            {'move_id': st_line.journal_entry_id.id,
                             'statement_id': st_line.statement_id.id})

        for rec_ids in move_reconcile_id:
            if len(rec_ids) >= 2:
                move_line_obj.reconcile_partial(cr, uid, rec_ids)

        update_ok = st_line.journal_id.update_posted
        if not update_ok:
            st_line.journal_id.write({'update_posted': True})
        move_obj.button_cancel(cr, uid, [move_id_old])
        st_line.journal_id.write({'update_posted': update_ok})
        move_obj.unlink(cr, uid, move_id_old)
        return res

    def _get_exchange_reconcile(
            self, cr, uid,
            move_line_tax, move_line_rec,
            amount_rec_payable, amount_unreconcile_rec_pay,
            parent, company_currency, statement_currency, context=None):

        move_line_obj = self.pool.get('account.move.line')
        currency_obj = self.pool.get('res.currency')

        move_counterpart = move_line_tax.get('move_line_reconcile', None)
        rec_ids = []

        move_line_counterpart = move_line_obj.browse(
            cr, uid, move_counterpart)[0]
        rec_ids.append(move_line_counterpart.id)

        for move_id_tax_payment in move_line_obj.browse(cr, uid, move_line_rec):
            if move_line_counterpart.account_id.id == move_id_tax_payment.account_id.id:
                move_line_payment_tax = move_id_tax_payment
                rec_ids.append(move_id_tax_payment.id)

        amount_tax_counterpart = move_line_counterpart.amount_residual

        prec = self.pool.get('decimal.precision').precision_get(
            cr, uid, 'Account')

        amount_tax_payment = abs(
            move_line_payment_tax.debit - move_line_payment_tax.credit)

        factor = context.get('factor_type', [1, 1])

        amount_residual = abs(amount_tax_counterpart)-abs(amount_tax_payment)

        if float_compare(amount_rec_payable, amount_unreconcile_rec_pay,
                         precision_digits=prec):
            amount_residual = 0.0
        else:
            amount_residual = amount_residual*factor[0]

        if not currency_obj.is_zero(
            cr, uid, parent.company_id.currency_id, amount_residual) and\
                move_line_payment_tax.currency_id:

            exch_lines = self._get_exchange_lines(
                cr, uid, parent, move_line_counterpart, move_line_payment_tax,
                amount_residual, company_currency, statement_currency,
                context=context)

            new_id = move_line_obj.create(cr, uid, exch_lines[0], context)
            move_line_exch_id = move_line_obj.create(
                cr, uid, exch_lines[1], context)
            rec_ids.append(new_id)

            return [[new_id, move_line_exch_id], rec_ids]
        return [[], rec_ids]

    def _get_factor_type(self, cr, uid, amount=False, ttype=False, context=None):
        if context is None:
            context = {}
        factor_type = [-1, 1]
        if (amount and amount < 0) or (ttype and ttype == 'payment'):
            factor_type = [1, -1]
        context['factor_type'] = factor_type
        return True

    def _get_move_line_counterpart(
            self, cr, uid, mv_line_dicts, company_currency, statement_currency,
            context=None):

        move_line_obj = self.pool.get('account.move.line')
        currency_obj = self.pool.get('res.currency')

        counterpart_unreconcile = 0
        counterpart_amount = 0
        statement_amount = 0
        for move_line_dict in mv_line_dicts:

            if move_line_dict.get('counterpart_move_line_id'):
                move_counterpart_id =\
                    move_line_dict.get('counterpart_move_line_id')

                move_line_id = move_line_obj.browse(
                    cr, uid, move_counterpart_id, context=context)

                if move_line_id.journal_id.type not in (
                        'sale_refund', 'purchase_refund'):

                    statement_amount += move_line_dict.get('credit') > 0 and\
                        move_line_dict.get('credit') or\
                        move_line_dict.get('debit')

                    counterpart_amount += move_line_id.amount_currency or\
                        move_line_id.credit > 0 and\
                        move_line_id.credit or move_line_id.debit

                    counterpart_unreconcile = currency_obj.compute(
                        cr, uid, company_currency, statement_currency,
                        abs(move_line_id.amount_residual), context=context)

                    if move_line_id.currency_id and\
                            move_line_id.currency_id.id == statement_currency:
                        counterpart_unreconcile = abs(
                            move_line_id.amount_residual_currency)

        return [statement_amount, counterpart_amount, counterpart_unreconcile]

    def _get_move_line_tax(self, cr, uid, mv_line_dicts, context=None):

        tax_obj = self.pool.get('account.tax')
        move_line_obj = self.pool.get('account.move.line')

        if context is None:
            context = {}

        dat = []
        factor = context.get('factor_type', [1, 1])
        account_group = {}
        move_line_ids = []

        counterpart_move_line_ids = [
            mv_line_dict.get('counterpart_move_line_id')
            for mv_line_dict in mv_line_dicts
            if mv_line_dict.get('counterpart_move_line_id')]

        for move_line in move_line_obj.browse(
                cr, uid, counterpart_move_line_ids, context=context):

            move_line_ids.extend(move_line_obj.search(
                cr, uid, [('move_id', '=', move_line.move_id.id)]))

        for move_line_id in move_line_obj.browse(cr, uid, move_line_ids):
            # En cada "aml" de poliza buscamos las que son referente a los
            # impuesto, validando que no sea una cuenta por pagar/cobrar y que
            # los movimientos no pertenescan a un diario de banco/efectivo
            # por que puede ser una poliza con iva efecttivamente pagado
            if move_line_id.account_id.type not in\
                    ('receivable', 'payable') and\
                    move_line_id.journal_id.type not in ('cash', 'bank'):
                account_group.setdefault(move_line_id.account_id.id, [0, 0])
                # Validacion del debit/credit cuando la poliza contiene
                # impuesto 0 o EXENTO toma el monto base de la linea de poliza
                if not move_line_id.debit and not move_line_id.credit:
                    account_group[move_line_id.account_id.id][0] +=\
                        move_line_id.amount_base or 0.0
                else:
                    # @factor puede ser 1 o -1 depende de tipo de transaccion
                    # si es venta, compra, nota de credito/debito, retenciones
                    # Esto para poder hacer la sumatoria de varios "aml" en un
                    # solo monto dependiendo de valor que quede +/- determina
                    # si se escribe por el lado del credit/debit
                    account_group[move_line_id.account_id.id][0] +=\
                        move_line_id.amount_currency*factor[0] or\
                        move_line_id.debit > 0 and\
                        move_line_id.debit*factor[0] or\
                        move_line_id.credit*factor[1]
                    account_group[move_line_id.account_id.id][1] = move_line_id.id

        for move_account_tax in account_group:
            amount_base_secondary = 0
            tax_ids = tax_obj.search(
                cr, uid,
                [('account_collected_id', '=', move_account_tax),
                 ('tax_voucher_ok', '=', True)], limit=1)

            if tax_ids:
                tax_id = tax_obj.browse(cr, uid, tax_ids[0])

                amount_ret_tax = self._get_retention(
                    cr, uid, account_group, tax_id)
                # Validacion especial para cuando la poliza contiene impuestos
                # 0% o EXENTO en lugar de tomar debit/credit toma el monto base
                # para reporta a la DIOT validando el @amount del impuesto
                if tax_id.amount == 0:
                    amount_total_tax = 0
                    amount_base_secondary = account_group.get(move_account_tax)[0]
                else:
                    amount_total_tax =\
                        account_group.get(move_account_tax)[0]+amount_ret_tax
                dat.append({
                    'account_tax_voucher':
                        tax_id.account_paid_voucher_id.id,
                    'account_tax_collected':
                        tax_id.account_collected_id.id,
                    'amount': amount_total_tax,
                    'tax_id': tax_id,
                    'tax_analytic_id':
                        tax_id.account_analytic_collected_id and
                        tax_id.account_analytic_collected_id.id or False,
                    'amount_base_secondary': amount_base_secondary,
                    'move_line_reconcile': [account_group.get(move_account_tax)[1]]
                    })
        return dat

    def _get_retention(self, cr, uid, account_group=None, tax=None):
        ''' Get retention of same type of category tax
            @param account_group: Dictionary with grouped by key of account_id
                and value amount fox example {1: 1.0}
            @param tax: Object Browse of tax '''

        tax_obj = self.pool.get('account.tax')
        amount_retention_tax = 0
        if account_group and tax:
            for move_account_tax in account_group:
                if tax.amount > 0:
                    tax_ids = tax_obj.search(
                        cr, uid,
                        [('account_collected_id', '=', move_account_tax),
                         ('tax_category_id.code', '=',
                            tax.tax_category_id.code),
                         ('amount', '<', 0), ('id', '<>', tax.id),
                         ], limit=1)
                    if tax_ids:
                        amount_retention_tax += account_group[move_account_tax][0]

        return amount_retention_tax


class account_bank_statement(osv.osv):

    _inherit = 'account.bank.statement'

    def button_journal_entries(self, cr, uid, ids, context=None):

        aml_obj = self.pool.get('account.move.line')
        move_line_ids = []

        res = super(account_bank_statement, self).button_journal_entries(
            cr, uid, ids, context=context)

        aml_id_statement = aml_obj.search(cr, uid, res.get('domain', []))
        for move_line in aml_obj.browse(cr, uid, aml_id_statement):
            for move_id in move_line.move_id.line_id:
                move_line_ids.append(move_id.id)

        res.update({'domain': [('id', 'in', list(set(move_line_ids)))]})
        return res
