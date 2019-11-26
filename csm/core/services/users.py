#!/usr/bin/env python3

"""
 ****************************************************************************
 Filename:          alerts.py
 Description:       Services for alerts handling 

 Creation Date:     09/05/2019
 Author:            Alexander Nogikh
                    Prathamesh Rodi
                    Oleg Babin

 Do NOT modify or remove this copyright and confidentiality notice!
 Copyright (c) 2001 - $Date: 2015/01/14 $ Seagate Technology, LLC.
 The code contained herein is CONFIDENTIAL to Seagate Technology, LLC.
 Portions are also trade secret. Any use, duplication, derivation, distribution
 or disclosure of this code, for any reason, not expressly authorized is
 prohibited. All other rights are expressly reserved by Seagate Technology, LLC.
 ****************************************************************************
"""
# Let it all reside in a separate controller until we've all agreed on request
# processing architecture
import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from csm.common.log import Log
from csm.common.services import Service, ApplicationService
from csm.common.queries import SortBy, SortOrder, QueryLimits, DateTimeRange
from csm.core.data.models.users import User, UserType
from csm.common.errors import CsmNotFoundError, CsmError, InvalidRequest
import time
from csm.core.data.db.db_provider import (DataBaseProvider, GeneralConfig)
from csm.core.data.access.filters import Compare, And, Or
from csm.core.data.access import Query, SortOrder
from csm.core.blogic import const
from schematics import Model
from schematics.types import StringType, BooleanType, IntType
from typing import Optional, Iterable


class UserManager:
    """
    The class encapsulates user management activities.
    This is intended to be used during user management and authorization
    """
    def __init__(self, storage: DataBaseProvider) -> None:
        self.storage = storage

    async def create(self, user: User) -> User:
        """
        Stores a new user
        :param user: User model instance
        """
        # validate the model
        existing_user = await self.get(user.user_id)
        if existing_user:
            raise InvalidRequest("Such user already exists", USERS_MSG_ALREADY_EXISTS)

        return await self.storage(User).store(user)

    async def get(self, user_id) -> User:
        """
        Fetches a single user.
        :param user_id: User identifier
        :returns: User object in case of success. None otherwise.
        """
        query = Query().filter_by(Compare(User.user_id, '=', user_id))
        return next(iter(await self.storage(User).get(query)), None)

    async def delete(self, user_id: str) -> None:
        await self.storage(User).delete(Compare(User.user_id, '=', user_id))

    async def get_list(self, offset: int = None, limit: int = None,
                       sort: SortBy = None) -> List[User]:
        """
        Fetches the list of users.
        :param offset: Number of items to skip.
        :param limit: Maximum number of items to return.
        :param sort: What field to sort on.
        :returns: A list of User models
        """
        query = Query()

        if offset:
            query = query.offset(offset)

        if limit:
            query = query.limit(limit)

        if sort:
            query = query.order_by(getattr(User, sort.field), sort.order)

        return await self.storage(User).get(query)

    async def count(self):
        return await self.storage(User).count(None)

    async def save(self, user: User):
        """
        Stores an already existing user.
        :param user:
        """
        # TODO: validate the model
        await self.storage(User).store(user)


USERS_MSG_USER_NOT_FOUND = "users_not_found"
USERS_MSG_ALREADY_EXISTS = "users_already_exists"
USERS_MSG_CANNOT_SORT = "users_non_sortable_field"


class CsmUserService(ApplicationService):
    """
    Service that exposes csm user management actions from the csm core.
    """
    def __init__(self, user_mgr: UserManager):
        self.user_mgr = user_mgr

    def _user_to_dict(self, user: User):
        """ Helper method to convert user model into a dictionary repreentation """
        return {
            "id": user.user_id,
            "username": user.user_id,
            "user_type": user.user_type,
            "interfaces": user.interfaces,
            "roles": user.roles,
            "temperature": user.temperature,
            "language": user.language,
            "timeout": user.timeout,
            "created_time": user.created_time.isoformat() + 'Z',
            "updated_time": user.updated_time.isoformat() + 'Z'
        }

    async def create_user(self, user_id: str, password: str, **kwargs) -> dict:
        """
        Handles the csm user creation
        :param user_id: User identifier
        :param user_password: User password (not hashed)
        :param roles: List of roles of the user
        :param interfaces: List of user interfaces
        :returns: A dictionary describing the newly created user.
        In case of error, an exception is raised.
        """
        user = User.instantiate_csm_user(user_id, password)
        user.update(kwargs)
        await self.user_mgr.create(user)
        return self._user_to_dict(user)

    async def get_user(self, user_id: str):
        """
        Fetches a single user.
        """
        user = await self.user_mgr.get(user_id)
        if not user:
            raise CsmNotFoundError("There is no such user", USERS_MSG_USER_NOT_FOUND)
        return self._user_to_dict(user)

    async def get_user_list(self, limit, offset, sort_by, sort_dir):
        """
        Fetches the list of existing users.
        """
        user_list = await self.user_mgr.get_list(offset or None, limit or None,
            SortBy(sort_by, SortOrder.ASC if sort_dir == "asc" else SortOrder.DESC))

        field_mapping = {
            "id": "user_id",
            "username": "user_id"
        }
        if sort_by in field_mapping:
            sort_by = field_mapping[sort_by]

        if sort_by and sort_by not in const.CSM_USER_SORTABLE_FIELDS:
            raise InvalidRequest("It is impossible to sort by this field", USERS_MSG_CANNOT_SORT)

        return [self._user_to_dict(x) for x in user_list]

    async def delete_user(self, user_id: str):
        """ User deletion """
        user = await self.user_mgr.get(user_id)
        if not user:
            raise CsmNotFoundError("There is no such user", USERS_MSG_USER_NOT_FOUND)
        await self.user_mgr.delete(user_id)
        return {}

    async def update_user(self, user_id: str, new_values: dict) -> dict:
        user = await self.user_mgr.get(user_id)
        if not user:
            raise CsmNotFoundError("There is no such user", USERS_MSG_USER_NOT_FOUND)

        user.update(new_values)
        if 'user_id' in new_values and new_values['user_id'] != user_id:
            # We have changed the user name
            await self.user_mgr.delete(user_id)
            await self.user_mgr.create(user)
        else:
            await self.user_mgr.save(user)
        return self._user_to_dict(user)