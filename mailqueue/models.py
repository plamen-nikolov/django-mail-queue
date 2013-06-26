#---------------------------------------------#
#
# Mailer will queue up emails, Try to send them
# and keep track of if they are sent or not.
# Should be executed with a cron job.
#
#---------------------------------------------#
import datetime
import logging
import os

logger = logging.getLogger(__name__)

from django.db import models
from django.core.mail import EmailMultiAlternatives
from django.db.models.signals import post_save
from django.db.models import F
from django.dispatch import receiver
from django.utils.translation import ugettext_lazy as _
from django.conf import settings


from . import defaults


class MailerMessageManager(models.Manager):
    
    def send_queued(self, limit=None):
        if limit is None:
            limit = getattr(settings, 'MAILQUEUE_LIMIT', defaults.MAILQUEUE_LIMIT)

        for email in self.filter(sent=False)[:limit]:
            email.send()

    def pending(self, limit=None):
        
        if limit is None:
            limit = getattr(settings, 'MAILQUEUE_LIMIT', defaults.MAILQUEUE_LIMIT)

        return self.filter(sent=False)[:limit]


class MailerMessage(models.Model):
    """

    """

    class Meta:
        verbose_name =_(u'Mailer message')
        verbose_name_plural =_(u'Mailer messages')

    subject             = models.CharField(max_length=250, blank=True, null=True)
    to_address          = models.EmailField(max_length=250)
    bcc_address         = models.EmailField(max_length=250, blank=True, null=True)
    from_address        = models.EmailField(max_length=250)
    content             = models.TextField(blank=True, null=True)
    html_content        = models.TextField(blank=True, null=True)
    app                 = models.CharField(max_length=250, blank=True, null=True)
    sent                = models.BooleanField(default=False, editable=False)

    attempts_count      = models.IntegerField(blank=False, null=False, default=0, verbose_name=_(u"Attempts count"))
    last_attempt        = models.DateTimeField(auto_now=False, auto_now_add=False, blank=True, null=True, editable=False)
    
    datetime_created    = models.DateTimeField(null=False, blank=False, auto_now=False, auto_now_add=True, editable=False, verbose_name=_(u"Date/time created"))

    objects = MailerMessageManager()


    def save(self, do_not_send=False, *args, **kwargs):
        """
        Saves the MailerMessage instance without sending the e-mail. This ensures
        other models (e.g. `Attachment`) have something to relate to in the database.
        """
        self.do_not_send = do_not_send
        super(MailerMessage, self).save(*args, **kwargs)


    def __unicode__(self):
        return self.subject



    def add_attachment(self, attachment):
        """
        Takes a Django `File` object and creates an attachment for this mailer message.
        """
        if self.pk is None:
            self.save(do_not_send=True)

        Attachment.objects.create(email=self, file_attachment=attachment)

    def send(self, raise_on_error=False):
        """
            :param raise_on_error:
                If message sending fails, exception must be raised to upper level
                function because of log in cronjob for example.
                BUT if exception raises on user message sending, no exception
                should be thrown.

        """
        if not self.sent:
            if getattr(settings, 'USE_TZ', False):
                # This change breaks SQLite usage.
                from django.utils.timezone import utc
                self.last_attempt = datetime.datetime.utcnow().replace(tzinfo=utc)
            else:
                self.last_attempt = datetime.datetime.now()

            self.attempts_count = F('attempts_count') + 1

            subject, from_email, to = self.subject, self.from_address, self.to_address
            text_content = self.content
            msg = EmailMultiAlternatives(subject, text_content, from_email, [to])
            if self.html_content:
                html_content = self.html_content
                msg.attach_alternative(html_content, "text/html")
            if self.bcc_address:
                if ',' in self.bcc_address:
                    msg.bcc = [ email.strip() for email in self.bcc_address.split(',') ]
                else:
                    msg.bcc = [self.bcc_address, ]

            # Add any additional attachments
            for attachment in self.attachment_set.all():
                msg.attach_file(os.path.join(settings.MEDIA_ROOT, attachment.file_attachment.name))
            try:
                msg.send()
                self.sent = True
            except Exception, e:
                self.do_not_send = True
                logger.error('Mail Queue: Message: "{0}", Exception: {1}'.format(self.id, e))
                if raise_on_error:
                    raise e

            self.save(do_not_send=True)


class Attachment(models.Model):
    file_attachment = models.FileField(upload_to='mail-queue/attachments', blank=True, null=True)
    email = models.ForeignKey(MailerMessage, blank=True, null=True)

    def __unicode__(self):
        return self.file_attachment.name

@receiver(post_save, sender=MailerMessage)
def send_post_save(sender, instance, signal, *args, **kwargs):
    if getattr(instance, "do_not_send", False):
        instance.do_not_send = False
        return

    if getattr(settings, 'MAILQUEUE_CELERY', defaults.MAILQUEUE_CELERY):
        from mailqueue.tasks import send_mail
        send_mail.delay(instance.pk)
    else:
        instance.send()
