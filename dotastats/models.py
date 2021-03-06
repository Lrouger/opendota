from datetime import datetime
from datetime import timedelta
from django.db import models
from django.db.models import Q
from django.core import serializers
from django.core.exceptions import ObjectDoesNotExist
from django.conf import settings
from django.utils.timezone import get_current_timezone
from django.utils import timezone

TIME_ZONE_SETTING = get_current_timezone()
MATCH_FRESHNESS = settings.DOTA_MATCH_REFRESH
PLAYER_FRESHNESS = settings.DOTA_PLAYER_REFRESH

"""
Tower Status:
0x400 = ANCIENT_TOP
0x200 = ANCIENT_BOTTOM
0x100 = TOP_3
0x80 = TOP_2
0x40 = TOP_1
0x20 = MIDDLE_3
0x10 = MIDDLE_2
0x8 = MIDDLE_1
0x4 = BOTTOM_3
0x2 = BOTTOM_2
0x1 = BOTTOM_1
"""

"""
Barracks Status:
0x20 = TOP_RANGED
0x10 = TOP_MELEE
0x8 = MID_RANGED
0x4 = MID_MELEE
0x2 = BOT_RANGED
0x1 = BOT_MELEE
"""


class SteamPlayer(models.Model):
    steamid = models.BigIntegerField(primary_key=True, unique=True)
    last_refresh = models.DateTimeField(auto_now=True, auto_now_add=True, db_index=True) # Last time this player was checked.
    personaname = models.TextField() # Don't index this. Will degrade the index quickly.
    profileurl = models.TextField(blank=True)
    avatar = models.TextField(blank=True)
    avatarmedium = models.TextField(blank=True)
    avatarfull = models.TextField(blank=True)
    lastlogoff = models.DateTimeField(null=True) # UNIX time of player last seen.
    
    def __unicode__(self):
        return self.personaname
    
    def get_steam_name(self):
        return self.personaname

    def get_id_or_url(self):
        if self.profileurl:
            return self.profileurl.split('/')[-2] # http://steamcommunity.com/profiles/XXXXXXXX/ <- Part we want is -2 index. 
        else:
            return self.steamid
    
    @staticmethod
    def get_by_id(communityid):
        """Returns a SteamPlayer object or None if does not exist.
        Args:
            communityid (int): CommunityID to retrieve.
        Returns:
            A SteamPlayer object, or None if it does not exist.
        """
        try:
            return SteamPlayer.objects.get(steamid=communityid)
        except ObjectDoesNotExist:
            return None
    
    @staticmethod
    def filter_by_name(name=None, profileurl=None, communityid=None, count=25):
        """Returns at most ``count`` accounts that match either name or profileurl or both.
        
        Kwargs:
            name (str): Name to match against.
            profileurl (str): ProfileURL to match against.
            communityid (int): CommunityID to match against. WARNING: Argument is ignored if non-integer. (Not garunteed to be single-result)
            count (int): Amount of results to limit to.
            
        Returns:
            At most a list of ``count`` SteamPlayer objects matched.
        """
        filter = Q()
        if name == None and profileurl == None and communityid == None:
            raise ValueError("Either name or profileurl or communityid must not be None.")
        if name:
            filter |= Q(personaname__icontains=name)
        if profileurl:
            filter |= Q(profileurl__iexact=profileurl)
        try:
            if communityid:
                filter |= Q(steamid=int(communityid))
        except ValueError:
            pass
        
        return SteamPlayer.objects.filter(filter)[:count]
    
    @staticmethod
    def get_refresh():
        """Returns at most 100 accounts to refresh. 
        
        Orders by last_refresh, oldest first.
        """
        return SteamPlayer.objects.filter(last_refresh__lt=(timezone.now() - PLAYER_FRESHNESS)).order_by('last_refresh')[:100]
    
    @staticmethod
    def from_json_response(json):
        return SteamPlayer(steamid = json.get('steamid'),
               personaname = json.get('personaname'),
               profileurl = json.get('profileurl'),
               avatar = json.get('avatar'),
               avatarmedium = json.get('avatarmedium'),
               avatarfull = json.get('avatarfull'),
               lastlogoff = None if json.get('lastlogoff', None) == None else datetime.fromtimestamp(json.get('lastlogoff')).replace(tzinfo=TIME_ZONE_SETTING))
      
# To refresh, use django-admin.py getitems
class Items(models.Model):
    item_id = models.IntegerField(primary_key=True) # From the client files.
    client_name = models.TextField()
    
    def __unicode__(self):
        return self.client_name
    
    def get_code_name(self):
        if 'recipe' in self.client_name:
            return 'recipe'
        else:
            return self.client_name[5:] # Ex: item_blink
    
# To refresh, use django-admin.py getheroes
class Heroes(models.Model):
    hero_id = models.IntegerField(primary_key=True) # From the client files.
    client_name = models.TextField()
    dota2_name = models.TextField()
    
    def __unicode__(self):
        return self.dota2_name
    
    def get_code_name(self):
        return self.client_name[14:] # Ex:  npc_dota_hero_chaos_knight
    
    def get_url(self):
        return self.dota2_name.replace(' ', '-') # Turns 'Phantom Lancer' into 'Phantom-Lancer'

# Long-running queue of matches to look up.
class MatchHistoryQueue(models.Model):
    match_id = models.BigIntegerField(primary_key=True, unique=True)
    last_refresh = models.DateTimeField(auto_now=True, auto_now_add=True) # Queue will empty FIFO
    match_seq_num = models.BigIntegerField()
    start_time = models.DateTimeField() # UNIX Timestamp
    lobby_type = models.IntegerField()
    
    def get_lobby_type(self):
        return get_lobby_type(self.lobby_type)
    
    @staticmethod
    def from_json_response(json): 
        return MatchHistoryQueue(
            match_id=json['match_id'],
            match_seq_num=json['match_seq_num'],
            start_time=datetime.fromtimestamp(json['start_time']).replace(tzinfo=TIME_ZONE_SETTING),
            lobby_type=json['lobby_type'],)
    class Meta:
        get_latest_by = "last_refresh"
    
class MatchHistoryQueuePlayers(models.Model):
    match_history_queue = models.ForeignKey('MatchHistoryQueue')
    account_id = models.ForeignKey(SteamPlayer, related_name='+', db_column='account_id', null=True)
    player_slot = models.IntegerField()
    hero_id = models.ForeignKey('Heroes', related_name='+', db_column='hero_id')
    # Custom tracking field
    is_bot = models.BooleanField(default=False)
    class Meta:
        unique_together = (('match_history_queue', 'hero_id', 'player_slot',),) # Every match, only one hero_id per player slot.
        ordering = ('player_slot',)
    
    @staticmethod
    def from_json_response(match_history_queue, json):
        return MatchHistoryQueuePlayers(
            match_history_queue=match_history_queue,
            account_id_id=steamapi.convertAccountNumbertoSteam64(json.get('account_id', None)), # Store all data in steam64. No reason to have Steam32.
            player_slot=json['player_slot'],
            hero_id_id=json['hero_id'],
            is_bot=True if json.get('account_id', None) == None else False,)

def get_game_type(game_mode):
    if game_mode == 1:
        return 'All Pick'
    elif game_mode == 2:
        return "Captain's Mode"
    elif game_mode == 3:
        return 'Random Draft'
    elif game_mode == 4:
        return 'Single Draft'
    elif game_mode == 5:
        return 'All Random'    
    elif game_mode == 6:
        return 'Death Mode'
    elif game_mode == 7:
        return 'Diretide'
    elif game_mode == 8:
        return "Reverse Captain's Mode"
    elif game_mode == 9:
        return 'The Greeviling'
    elif game_mode == 10:
        return 'Tutorial'
    elif game_mode == 11:
        return 'Mid Only'
    elif game_mode == 12:
        return 'Least Played'
    elif game_mode == 13:
        return 'New Player Pool'
    else:
        return str(game_mode) 
        
def get_lobby_type(lobby_type):
    if lobby_type == 0:
        return 'Public Match'
    elif lobby_type == 1:
        return 'Practice Match'
    elif lobby_type == 2:
        return 'Tournament Match'
    elif lobby_type == 3:
        return 'Tutorial'
    elif lobby_type == 4:
        return 'Co-op Bot Match'
    elif lobby_type == 5:
        return 'Team Match'
    else:
        return str(lobby_type)

class MatchDetails(models.Model):
    match_id = models.BigIntegerField(primary_key=True, unique=True)
    last_refresh = models.DateTimeField(auto_now=True, auto_now_add=True) # Last time this data was accessed for freshness.
    match_seq_num = models.BigIntegerField()
    season = models.IntegerField()
    radiant_win = models.BooleanField()
    duration = models.IntegerField() # Seconds of match
    start_time = models.DateTimeField() # UNIX Timestamp
    tower_status_radiant = models.IntegerField()
    tower_status_dire = models.IntegerField()
    barracks_status_radiant = models.IntegerField()
    barracks_status_dire = models.IntegerField()
    cluster = models.IntegerField()
    first_blood_time = models.IntegerField()
    lobby_type = models.IntegerField()
    human_players = models.IntegerField()
    leagueid = models.IntegerField()
    positive_votes = models.IntegerField()
    negative_votes = models.IntegerField()
    game_mode = models.IntegerField()
    
    @staticmethod
    def exclude_low_priority():
        """Returns only MatchDetails objects that are worth mentioning.
        
        Currently excludes all matches marked Private, all matches without 10 players, and all matches less than 4 minutes long. 
        """
        return MatchDetails.objects.exclude(Q(lobby_type=4) | Q(human_players__lt=10) | Q(duration__lt=480) | (Q(tower_status_dire=2047) & Q(tower_status_radiant=2047)))
    
    @staticmethod
    def get_refresh():
        """Returns a single MatchDetails that is ready to be accessed again for freshness, or None if nothing is to be refreshed. 
        
        Orders by last_refresh, oldest first.
        
        (Required since DotA2 Web API gives invalid account_id if account private.)
        """
        try:
            return MatchDetails.objects.filter(last_refresh__lt=(timezone.now() - MATCH_FRESHNESS)).order_by('last_refresh')[0]
        except IndexError:
            return None
        
    def get_duration(self):
        """Returns duration of match in H:M:S. """
        return str(timedelta(seconds=self.duration))
    
    def get_lobby_type(self):
        return get_lobby_type(self.lobby_type)
    
    def get_game_type(self):
        return get_game_type(self.game_mode)
    
    def get_players(self):
        """Returns all players in the match."""
        return self.matchdetailsplayerentry_set.all()
    
    def get_dire_players(self):
        """Returns dire players in the match."""
        return self.matchdetailsplayerentry_set.filter(player_slot__gte=100)
    
    def get_radiant_players(self):
        """Returns radiant players in the match."""
        return self.matchdetailsplayerentry_set.filter(player_slot__lt=100)
    
    def drop_json_debug(self):
        """Returns ugly looking json of the object."""
        return serializers.serialize("json", [self], indent=4)
    
    @staticmethod
    def from_json_response(json):
        return MatchDetails(
            match_id = json['match_id'],
            match_seq_num=json['match_seq_num'],
            season = json['season'],
            radiant_win = json['radiant_win'],
            duration = json['duration'],
            start_time = datetime.fromtimestamp(json['start_time']).replace(tzinfo=TIME_ZONE_SETTING),
            tower_status_radiant = json['tower_status_radiant'],
            tower_status_dire = json['tower_status_dire'],
            barracks_status_radiant = json['barracks_status_radiant'],
            barracks_status_dire = json['barracks_status_dire'],
            cluster = json['cluster'],
            first_blood_time = json['first_blood_time'],
            lobby_type = json['lobby_type'],
            human_players = json['human_players'],
            leagueid = json['leagueid'],
            positive_votes = json['positive_votes'],
            negative_votes = json['negative_votes'],
            game_mode = json['game_mode']) 
    
    class Meta:
        ordering = ('-match_id',)

class MatchPicksBans(models.Model):
    match_details = models.ForeignKey('MatchDetails')
    is_pick = models.BooleanField()
    hero_id = models.ForeignKey('Heroes', related_name='+', db_column='hero_id')
    team = models.IntegerField()
    order = models.IntegerField()
    
    @staticmethod
    def from_json_response(match_details, json):
        return MatchPicksBans(
              match_details = match_details,
              is_pick=json['is_pick'],
              hero_id_id=json['hero_id'],
              team=json['team'],
              order=json['order'],)    
    class Meta:
        unique_together = (('match_details', 'hero_id', 'order',),) # One hero per order per match.
        ordering = ('order',)
        
class MatchDetailsPlayerEntry(models.Model):
    match_details = models.ForeignKey('MatchDetails')
    account_id = models.ForeignKey(SteamPlayer, related_name='+', db_column='account_id', null=True)
    player_slot = models.IntegerField()
    hero_id = models.ForeignKey('Heroes', related_name='+', db_column='hero_id')
    item_0 = models.ForeignKey('Items', related_name='+', db_column='item_0', null=True)
    item_1 = models.ForeignKey('Items', related_name='+', db_column='item_1', null=True)
    item_2 = models.ForeignKey('Items', related_name='+', db_column='item_2', null=True)
    item_3 = models.ForeignKey('Items', related_name='+', db_column='item_3', null=True)
    item_4 = models.ForeignKey('Items', related_name='+', db_column='item_4', null=True)
    item_5 = models.ForeignKey('Items', related_name='+', db_column='item_5', null=True)
    kills = models.BigIntegerField()
    deaths = models.BigIntegerField()
    assists = models.BigIntegerField()
    leaver_status = models.IntegerField(null=True)
    gold = models.BigIntegerField()
    last_hits = models.BigIntegerField()
    denies = models.BigIntegerField()
    gold_per_min = models.BigIntegerField()
    xp_per_min = models.BigIntegerField()
    gold_spent = models.BigIntegerField()
    hero_damage = models.BigIntegerField()
    tower_damage = models.BigIntegerField() # WARNING: Some IntegerField models need to be BigIntegerField. MatchID: 59622 has invalid tower_damage for Earthshaker.
    hero_healing = models.BigIntegerField()
    level = models.IntegerField()
    ability_upgrades = models.TextField(null=True) # NOTE: Ability upgrades are stored in raw JSON format because they don't need indexing or aggregation.
    additional_units = models.TextField(null=True) # NOTE: Additional units (aka: Syllabear's spirit bear) are stored in raw JSON format because they don't need indexing or aggregation.
    # Custom tracking fields
    is_bot = models.BooleanField(default=False)
    
    def get_steam_name(self):
        return steamapi.GetPlayerName(self.account_id_id)
        
    @staticmethod
    def from_json_response(match_details, json):
        if json['hero_id'] == 0: # Legacy compatibility designating empty player slot. 
            return None
        return MatchDetailsPlayerEntry(
                match_details = match_details,
                account_id_id=steamapi.convertAccountNumbertoSteam64(json.get('account_id', None)), # Store all data in steam64. No reason to have Steam32.
                player_slot=json['player_slot'],
                hero_id_id=json['hero_id'],
                item_0_id=json['item_0'],
                item_1_id=json['item_1'],
                item_2_id=json['item_2'],
                item_3_id=json['item_3'],
                item_4_id=json['item_4'],
                item_5_id=json['item_5'],
                kills=json['kills'],
                deaths=json['deaths'],
                assists=json['assists'],
                leaver_status=json.get('leaver_status', None),
                gold=json['gold'],
                last_hits=json['last_hits'],
                denies=json['denies'],
                gold_per_min=json['gold_per_min'],
                xp_per_min=json['xp_per_min'],
                gold_spent=json['gold_spent'],
                hero_damage=json['hero_damage'],
                tower_damage=json['tower_damage'],
                hero_healing=json['hero_healing'],
                level=json['level'],
                ability_upgrades=json.get('ability_upgrades', None), # ability_upgrades can be null. 
                additional_units=json.get('additional_units', None),
                is_bot=True if json.get('account_id', None) == None or json.get('leaver_status', None) == None else False,)
        
    class Meta:
        unique_together = (('match_details', 'hero_id', 'player_slot',),) # Every match, only one hero_id per player slot.
        ordering = ('player_slot',)

class MatchSequenceNumber(models.Model):
    """This model exists as a persistent object. 
    The intended usage is to use get_last_match_seq_num and set_last_match_seq_num static methods."""
    last_match_seq_num = models.BigIntegerField(default=0)
    
    @staticmethod
    def get_last_match_seq_num():
        """Gets the record in this model. If it does not exist, or has not yet been initialized, returns default 0."""
        return MatchSequenceNumber.objects.get_or_create(pk=1)[0].last_match_seq_num
            
    @staticmethod
    def set_last_match_seq_num(last_seq_num):
        """Sets the record in this model."""
        MatchSequenceNumber(pk=1, last_match_seq_num=last_seq_num).save()
        
from dotastats.json import steamapi
